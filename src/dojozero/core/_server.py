"""FastAPI server for DojoZero Dashboard with WebSocket streaming.

This module implements:
- REST endpoints for trial listing and historical replay
- WebSocket endpoint for real-time event streaming
- EventBroadcaster for pushing events to connected clients
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ._dashboard import (
    Dashboard,
    TrialNotFoundError,
)
from ._types import StreamEvent

LOGGER = logging.getLogger("dojozero.server")


# =============================================================================
# WebSocket Message Types
# =============================================================================


class WSMessageType:
    SNAPSHOT = "snapshot"
    EVENT = "event"
    TRIAL_ENDED = "trial_ended"
    HEARTBEAT = "heartbeat"


# =============================================================================
# EventBroadcaster
# =============================================================================


@dataclass
class EventBroadcaster:
    """Manages WebSocket clients and broadcasts events by trial_id.

    Thread-safe broadcaster that:
    - Maintains WebSocket connections grouped by trial_id
    - Pushes events to all clients subscribed to a trial
    - Handles client disconnection gracefully
    """

    _clients: dict[str, set[WebSocket]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _event_history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _max_history: int = 100

    async def subscribe(self, trial_id: str, websocket: WebSocket) -> None:
        """Add a WebSocket client to a trial's subscriber list."""
        async with self._lock:
            if trial_id not in self._clients:
                self._clients[trial_id] = set()
            self._clients[trial_id].add(websocket)
        LOGGER.debug(
            "Client subscribed to trial '%s' (total: %d)",
            trial_id,
            len(self._clients.get(trial_id, set())),
        )

    async def unsubscribe(self, trial_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket client from a trial's subscriber list."""
        async with self._lock:
            if trial_id in self._clients:
                self._clients[trial_id].discard(websocket)
                if not self._clients[trial_id]:
                    del self._clients[trial_id]
        LOGGER.debug("Client unsubscribed from trial '%s'", trial_id)

    async def broadcast_event(self, trial_id: str, event_data: dict[str, Any]) -> None:
        """Broadcast an event to all clients subscribed to a trial."""
        message = {
            "type": WSMessageType.EVENT,
            "trial_id": trial_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": event_data,
        }

        # Store in history for new connections
        async with self._lock:
            if trial_id not in self._event_history:
                self._event_history[trial_id] = []
            self._event_history[trial_id].append(event_data)
            # Keep only recent events
            if len(self._event_history[trial_id]) > self._max_history:
                self._event_history[trial_id] = self._event_history[trial_id][
                    -self._max_history :
                ]

        await self._send_to_trial(trial_id, message)

    async def broadcast_trial_ended(self, trial_id: str) -> None:
        """Notify all clients that a trial has ended."""
        message = {
            "type": WSMessageType.TRIAL_ENDED,
            "trial_id": trial_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._send_to_trial(trial_id, message)

    async def send_snapshot(
        self, trial_id: str, websocket: WebSocket, snapshot: dict[str, Any]
    ) -> None:
        """Send a snapshot message to a specific client."""
        message = {
            "type": WSMessageType.SNAPSHOT,
            "trial_id": trial_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": snapshot,
        }
        await self._send_to_client(websocket, message)

    async def get_recent_events(self, trial_id: str) -> list[dict[str, Any]]:
        """Get recent events for a trial (for snapshot)."""
        async with self._lock:
            return list(self._event_history.get(trial_id, []))

    async def _send_to_trial(self, trial_id: str, message: dict[str, Any]) -> None:
        """Send a message to all clients subscribed to a trial."""
        async with self._lock:
            clients = list(self._clients.get(trial_id, set()))

        if not clients:
            return

        text = json.dumps(message, default=str)
        disconnected: list[WebSocket] = []

        for websocket in clients:
            try:
                await websocket.send_text(text)
            except Exception:
                disconnected.append(websocket)

        # Clean up disconnected clients
        for ws in disconnected:
            await self.unsubscribe(trial_id, ws)

    async def _send_to_client(
        self, websocket: WebSocket, message: dict[str, Any]
    ) -> None:
        """Send a message to a specific client."""
        try:
            text = json.dumps(message, default=str)
            await websocket.send_text(text)
        except Exception as e:
            LOGGER.warning("Failed to send message to client: %s", e)


# =============================================================================
# Server State
# =============================================================================


@dataclass
class ServerState:
    """Shared state for the FastAPI server."""

    dashboard: Dashboard
    broadcaster: EventBroadcaster = field(default_factory=EventBroadcaster)
    # Callback for actor events
    _event_hooks: dict[str, Callable[[StreamEvent], None]] = field(default_factory=dict)


_server_state: ServerState | None = None


def get_server_state() -> ServerState:
    """Get the current server state."""
    if _server_state is None:
        raise RuntimeError("Server not initialized")
    return _server_state


# =============================================================================
# Snapshot Builder
# =============================================================================


async def build_trial_snapshot(
    dashboard: Dashboard,
    trial_id: str,
    broadcaster: EventBroadcaster,
) -> dict[str, Any]:
    """Build a snapshot of the current trial state for new WebSocket connections.

    This provides all information needed to initialize the frontend UI.
    """
    try:
        status = dashboard.get_trial_status(trial_id)
    except TrialNotFoundError:
        return {
            "error": "Trial not found",
            "trial_id": trial_id,
        }

    # Get recent events from broadcaster history
    recent_events = await broadcaster.get_recent_events(trial_id)

    # Build agent summaries from trial status
    agents = []
    for actor_status in status.actors:
        if actor_status.role.value == "agent":
            agents.append(
                {
                    "actor_id": actor_status.actor_id,
                    "phase": actor_status.phase.value,
                }
            )

    snapshot = {
        "metadata": dict(status.metadata),
        "phase": status.phase.value,
        "agents": agents,
        "recent_events": recent_events[-50:],  # Last 50 events
    }

    return snapshot


# =============================================================================
# API Handlers
# =============================================================================


def create_app(
    dashboard: Dashboard, broadcaster: EventBroadcaster | None = None
) -> FastAPI:
    """Create the FastAPI application with all routes configured."""

    if broadcaster is None:
        broadcaster = EventBroadcaster()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _server_state
        _server_state = ServerState(dashboard=dashboard, broadcaster=broadcaster)
        LOGGER.info("Server started")
        yield
        LOGGER.info("Server shutting down")

    app = FastAPI(
        title="DojoZero Dashboard API",
        description="REST and WebSocket API for DojoZero trial management",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------------------------
    # REST Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/trials")
    async def list_trials() -> JSONResponse:
        """List all known trials with their status."""
        state = get_server_state()
        trials = state.dashboard.list_trials()

        result = []
        for trial_status in trials:
            trial_info = {
                "id": trial_status.trial_id,
                "phase": trial_status.phase.value,
                "metadata": dict(trial_status.metadata),
                "agents": [
                    {
                        "actor_id": actor.actor_id,
                        "role": actor.role.value,
                        "phase": actor.phase.value,
                    }
                    for actor in trial_status.actors
                    if actor.role.value == "agent"
                ],
            }
            result.append(trial_info)

        return JSONResponse(content=result)

    @app.get("/api/trials/{trial_id}/replay")
    async def get_trial_replay(trial_id: str) -> JSONResponse:
        """Get complete event history for a finished trial.

        Used by frontend for historical replay of completed trials.
        """
        state = get_server_state()

        try:
            status = state.dashboard.get_trial_status(trial_id)
        except TrialNotFoundError:
            return JSONResponse(
                content={"error": f"Trial '{trial_id}' not found"},
                status_code=404,
            )

        # Get all events from broadcaster history
        events = await state.broadcaster.get_recent_events(trial_id)

        # Also try to load from checkpoints if available
        checkpoint_events: list[dict[str, Any]] = []
        agent_states: dict[str, Any] = {}
        try:
            checkpoints = state.dashboard.list_checkpoints(trial_id)
            if checkpoints:
                # Get the latest checkpoint
                latest = max(checkpoints, key=lambda c: c.created_at)
                checkpoint = state.dashboard.load_checkpoint(latest.checkpoint_id)
                # Extract events from actor states (data streams)
                for actor_id, actor_state in checkpoint.actor_states.items():
                    if isinstance(actor_state, dict):
                        # Extract events
                        if "events" in actor_state:
                            stored_events = actor_state.get("events", [])
                            if isinstance(stored_events, list):
                                for evt in stored_events:
                                    if isinstance(evt, dict):
                                        checkpoint_events.append(evt)
                        # Extract agent state (conversation history)
                        if "state" in actor_state:
                            agent_states[actor_id] = actor_state.get("state", [])
        except Exception:
            pass  # Best effort

        # Combine events
        all_events = events + checkpoint_events

        # Sort events by timestamp if available
        def get_timestamp(e: dict) -> str:
            return e.get("timestamp", "") or ""

        all_events.sort(key=get_timestamp)

        return JSONResponse(
            content={
                "trial_id": trial_id,
                "phase": status.phase.value,
                "metadata": dict(status.metadata),
                "events": all_events,
                "agent_states": agent_states,
            }
        )

    # -------------------------------------------------------------------------
    # WebSocket Endpoint
    # -------------------------------------------------------------------------

    @app.websocket("/ws/trials/{trial_id}/stream")
    async def trial_stream(websocket: WebSocket, trial_id: str):
        """WebSocket endpoint for real-time trial event streaming.

        Protocol:
        - Server sends 'snapshot' immediately upon connection
        - Server pushes 'event' messages as events occur
        - Server sends 'trial_ended' when trial completes
        - Client does not need to send any messages
        """
        state = get_server_state()
        await websocket.accept()
        LOGGER.info("WebSocket connection accepted for trial '%s'", trial_id)

        try:
            # Subscribe to events
            await state.broadcaster.subscribe(trial_id, websocket)

            # Send initial snapshot
            snapshot = await build_trial_snapshot(
                state.dashboard, trial_id, state.broadcaster
            )
            await state.broadcaster.send_snapshot(trial_id, websocket, snapshot)

            # Keep connection alive and wait for disconnect
            while True:
                try:
                    # Wait for any message (for ping/pong or client disconnect)
                    # We don't expect client messages, but we need to keep the connection alive
                    await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Send heartbeat
                    try:
                        heartbeat = {
                            "type": WSMessageType.HEARTBEAT,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        await websocket.send_text(json.dumps(heartbeat))
                    except Exception:
                        break

        except WebSocketDisconnect:
            LOGGER.info("WebSocket disconnected for trial '%s'", trial_id)
        except Exception as e:
            LOGGER.error("WebSocket error for trial '%s': %s", trial_id, e)
        finally:
            await state.broadcaster.unsubscribe(trial_id, websocket)

    # -------------------------------------------------------------------------
    # Health Check
    # -------------------------------------------------------------------------

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok"}

    return app


# =============================================================================
# Event Hook for Dashboard Integration
# =============================================================================


def create_event_hook(broadcaster: EventBroadcaster, trial_id: str):
    """Create an event hook that broadcasts events through the broadcaster.

    This hook should be registered with Dashboard actors to push events
    to WebSocket clients in real-time.
    """

    async def hook(event: StreamEvent) -> None:
        # Convert StreamEvent to dict for broadcasting
        event_data = {
            "stream_id": event.stream_id,
            "emitted_at": event.emitted_at.isoformat(),
            "sequence": event.sequence,
            "metadata": dict(event.metadata) if event.metadata else {},
        }

        # Handle payload based on type
        payload = event.payload
        if hasattr(payload, "to_dict"):
            event_data["payload"] = payload.to_dict()
        elif hasattr(payload, "__dict__"):
            event_data["payload"] = {
                k: v for k, v in payload.__dict__.items() if not k.startswith("_")
            }
        else:
            event_data["payload"] = str(payload)

        await broadcaster.broadcast_event(trial_id, event_data)

    return hook


# =============================================================================
# Server Runner
# =============================================================================


async def run_server(
    dashboard: Dashboard,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run the FastAPI server with uvicorn.

    Args:
        dashboard: Dashboard instance to serve
        host: Host to bind to
        port: Port to listen on
    """
    import uvicorn

    broadcaster = EventBroadcaster()
    app = create_app(dashboard, broadcaster)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


__all__ = [
    "EventBroadcaster",
    "ServerState",
    "WSMessageType",
    "build_trial_snapshot",
    "create_app",
    "create_event_hook",
    "get_server_state",
    "run_server",
]
