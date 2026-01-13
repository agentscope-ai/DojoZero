"""Frontend Server for DojoZero.

This module implements the Frontend Server which is responsible for:
- Reading traces from Trace Store (Jaeger)
- Pushing OTel spans to browsers via WebSocket
- Serving React static files (optional, for production)

The Frontend Server is a read-only service that only queries the trace store.
It does not communicate with the Dashboard Server directly.

Endpoints:
- GET  /api/trials                    - List trials with metadata
- GET  /api/trials/{trial_id}         - Get trial info and spans
- WS   /ws/trials/{trial_id}/stream   - Real-time span stream

Configuration:
    dojo0 frontend --trace-store http://localhost:16686
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ._tracing import (
    JaegerTraceReader,
    SpanData,
    TraceReader,
)

LOGGER = logging.getLogger("dojozero.frontend_server")


class WSMessageType:
    SNAPSHOT = "snapshot"
    SPAN = "span"
    TRIAL_ENDED = "trial_ended"
    HEARTBEAT = "heartbeat"


@dataclass
class SpanBroadcaster:
    """Manages WebSocket clients and broadcasts spans by trial_id."""

    _clients: dict[str, set[WebSocket]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

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

    async def broadcast_span(self, trial_id: str, span: SpanData) -> None:
        """Broadcast a span to all clients subscribed to a trial."""
        message = {
            "type": WSMessageType.SPAN,
            "trial_id": trial_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": span.to_dict(),
        }
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
        self,
        trial_id: str,
        websocket: WebSocket,
        spans: list[SpanData],
    ) -> None:
        """Send a snapshot of recent spans to a specific client."""
        message = {
            "type": WSMessageType.SNAPSHOT,
            "trial_id": trial_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "spans": [span.to_dict() for span in spans],
            },
        }
        await self._send_to_client(websocket, message)

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

        for ws in disconnected:
            await self.unsubscribe(trial_id, ws)

    async def _send_to_client(
        self,
        websocket: WebSocket,
        message: dict[str, Any],
    ) -> None:
        """Send a message to a specific client."""
        try:
            text = json.dumps(message, default=str)
            await websocket.send_text(text)
        except Exception as e:
            LOGGER.warning("Failed to send message to client: %s", e)


@dataclass
class FrontendServerState:
    """Shared state for the Frontend Server."""

    trace_reader: TraceReader
    broadcaster: SpanBroadcaster = field(default_factory=SpanBroadcaster)
    static_dir: Path | None = None
    poll_interval: float = 1.0  # Seconds between trace polls

    # Tracking last poll time per trial for incremental updates
    _last_poll: dict[str, datetime] = field(default_factory=dict)


_server_state: FrontendServerState | None = None


def get_server_state() -> FrontendServerState:
    """Get the current server state."""
    if _server_state is None:
        raise RuntimeError("Server not initialized")
    return _server_state


def create_trace_reader(trace_store_url: str) -> TraceReader:
    """Create a JaegerTraceReader for the given URL.

    Args:
        trace_store_url: URL to Jaeger trace store (e.g., http://localhost:16686)
    """
    LOGGER.info("Using Jaeger trace reader for %s", trace_store_url)
    return JaegerTraceReader(trace_store_url)


async def _extract_trial_info_from_traces(
    trace_reader: TraceReader,
    trial_id: str,
) -> dict[str, Any]:
    """Extract trial phase and metadata from trace spans.

    Returns:
        dict with "phase" and "metadata" extracted from spans
    """
    try:
        spans = await trace_reader.get_spans(trial_id)
    except Exception as e:
        LOGGER.warning("Failed to get spans for trial '%s': %s", trial_id, e)
        return {"phase": "unknown", "metadata": {}}

    has_started = False
    has_stopped = False
    latest_start_time = 0
    latest_stop_time = 0

    # Metadata to extract from spans
    metadata: dict[str, Any] = {}

    for span in spans:
        op_name = span.operation_name
        tags = span.tags

        # Check lifecycle spans
        if op_name == "trial.started":
            has_started = True
            if span.start_time > latest_start_time:
                latest_start_time = span.start_time
        elif op_name in ("trial.stopped", "trial.terminated"):
            has_stopped = True
            if span.start_time > latest_stop_time:
                latest_stop_time = span.start_time

        # Extract game metadata from game_update spans
        elif op_name == "game_update":
            # Try to get team info from event tags
            home_team = tags.get("event.home_team")
            away_team = tags.get("event.away_team")

            if isinstance(home_team, dict):
                if home_team.get("teamTricode"):
                    metadata["home_team_tricode"] = home_team["teamTricode"]
                if home_team.get("teamName"):
                    metadata["home_team_name"] = home_team["teamName"]
            elif isinstance(home_team, str):
                # Try to parse JSON string
                try:
                    parsed = json.loads(home_team)
                    if isinstance(parsed, dict):
                        if parsed.get("teamTricode"):
                            metadata["home_team_tricode"] = parsed["teamTricode"]
                        if parsed.get("teamName"):
                            metadata["home_team_name"] = parsed["teamName"]
                except (json.JSONDecodeError, TypeError):
                    pass

            if isinstance(away_team, dict):
                if away_team.get("teamTricode"):
                    metadata["away_team_tricode"] = away_team["teamTricode"]
                if away_team.get("teamName"):
                    metadata["away_team_name"] = away_team["teamName"]
            elif isinstance(away_team, str):
                try:
                    parsed = json.loads(away_team)
                    if isinstance(parsed, dict):
                        if parsed.get("teamTricode"):
                            metadata["away_team_tricode"] = parsed["teamTricode"]
                        if parsed.get("teamName"):
                            metadata["away_team_name"] = parsed["teamName"]
                except (json.JSONDecodeError, TypeError):
                    pass

        # Extract game info from game_initialize spans
        elif op_name == "game_initialize":
            if tags.get("event.home_team"):
                metadata["home_team"] = tags["event.home_team"]
            if tags.get("event.away_team"):
                metadata["away_team"] = tags["event.away_team"]
            if tags.get("event.game_id"):
                metadata["game_id"] = tags["event.game_id"]

    # Determine phase
    if has_stopped and latest_stop_time >= latest_start_time:
        phase = "stopped"
    elif has_started and not has_stopped:
        phase = "running"
    elif has_stopped:
        phase = "stopped"
    elif spans:
        phase = "running"
    else:
        phase = "unknown"

    return {"phase": phase, "metadata": metadata}


def create_frontend_app(
    trace_store_url: str,
    static_dir: Path | None = None,
    poll_interval: float = 1.0,
) -> FastAPI:
    """Create the Frontend Server FastAPI application.

    Args:
        trace_store_url: URL to trace store (Jaeger)
        static_dir: Path to static files (React build output)
        poll_interval: Interval for polling new spans
    """
    trace_reader = create_trace_reader(trace_store_url)
    broadcaster = SpanBroadcaster()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _server_state
        _server_state = FrontendServerState(
            trace_reader=trace_reader,
            broadcaster=broadcaster,
            static_dir=static_dir,
            poll_interval=poll_interval,
        )
        LOGGER.info(
            "Frontend Server started (trace_store: %s, static_dir: %s)",
            trace_store_url,
            static_dir,
        )
        yield
        # Cleanup
        close_fn = getattr(trace_reader, "close", None)
        if close_fn is not None:
            await close_fn()
        LOGGER.info("Frontend Server shutting down")

    app = FastAPI(
        title="DojoZero Frontend Server",
        description="WebSocket streaming and trace queries for frontend",
        version="0.1.0",
        lifespan=lifespan,
    )

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
        """List trials with metadata extracted from traces."""
        state = get_server_state()

        # Get trial list from trace store and extract phase + metadata from spans
        trial_ids = await state.trace_reader.list_trials()

        # Build result with phase and metadata extracted from traces
        result = []
        for tid in trial_ids:
            trial_info_extracted = await _extract_trial_info_from_traces(
                state.trace_reader, tid
            )
            trial_info = {
                "id": tid,
                "phase": trial_info_extracted["phase"],
                "metadata": trial_info_extracted["metadata"],
            }
            result.append(trial_info)

        return JSONResponse(content=result)

    @app.get("/api/trials/{trial_id}")
    async def get_trial(trial_id: str) -> JSONResponse:
        """Get trial info and spans."""
        state = get_server_state()
        spans = await state.trace_reader.get_spans(trial_id)

        if not spans:
            # Check if trial exists (may have no spans yet)
            trial_ids = await state.trace_reader.list_trials()
            if trial_id not in trial_ids:
                return JSONResponse(
                    content={"error": f"Trial '{trial_id}' not found"},
                    status_code=404,
                )

        return JSONResponse(
            content={
                "trial_id": trial_id,
                "spans": [span.to_dict() for span in spans],
            }
        )

    # -------------------------------------------------------------------------
    # WebSocket Endpoint for Real-time Streaming
    # -------------------------------------------------------------------------

    @app.websocket("/ws/trials/{trial_id}/stream")
    async def trial_stream(websocket: WebSocket, trial_id: str):
        """WebSocket endpoint for real-time span streaming.

        Protocol:
        - Server sends 'snapshot' immediately upon connection
        - Server pushes 'span' messages as new spans are detected
        - Server sends 'trial_ended' when trial completes
        - Server sends 'heartbeat' periodically
        """
        state = get_server_state()
        await websocket.accept()
        LOGGER.info("WebSocket connection accepted for trial '%s'", trial_id)

        try:
            await state.broadcaster.subscribe(trial_id, websocket)

            # Send initial snapshot
            spans = await state.trace_reader.get_spans(trial_id)
            await state.broadcaster.send_snapshot(trial_id, websocket, spans)

            # Track seen span IDs to avoid duplicates
            seen_span_ids: set[str] = {s.span_id for s in spans}

            # Track last seen timestamp for efficient querying
            last_time = datetime.now(timezone.utc)
            if spans:
                # Get the latest span timestamp
                last_us = max(s.start_time for s in spans)
                last_time = datetime.fromtimestamp(last_us / 1_000_000, tz=timezone.utc)

            # Poll for new spans and broadcast
            while True:
                try:
                    # Wait for either a client message or timeout
                    await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=state.poll_interval,
                    )
                except asyncio.TimeoutError:
                    # Poll for new spans (since last_time for efficiency)
                    new_spans = await state.trace_reader.get_spans(
                        trial_id, since=last_time
                    )

                    # Filter out already-seen spans (double protection)
                    truly_new_spans = [
                        s for s in new_spans if s.span_id not in seen_span_ids
                    ]

                    # Broadcast only new spans
                    for span in truly_new_spans:
                        await state.broadcaster.broadcast_span(trial_id, span)
                        seen_span_ids.add(span.span_id)

                    if truly_new_spans:
                        last_us = max(s.start_time for s in truly_new_spans)
                        last_time = datetime.fromtimestamp(
                            last_us / 1_000_000, tz=timezone.utc
                        )
                        LOGGER.debug(
                            "Sent %d new spans for trial '%s'",
                            len(truly_new_spans),
                            trial_id,
                        )

                    # Send heartbeat
                    heartbeat = {
                        "type": WSMessageType.HEARTBEAT,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    await websocket.send_text(json.dumps(heartbeat))

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
        state = get_server_state()
        return {
            "status": "ok",
            "static_dir": str(state.static_dir) if state.static_dir else None,
        }

    # -------------------------------------------------------------------------
    # Static File Serving (SPA support)
    # -------------------------------------------------------------------------

    if static_dir and static_dir.exists():
        # Serve static files
        app.mount(
            "/assets",
            StaticFiles(directory=static_dir / "assets"),
            name="assets",
        )

        @app.get("/{path:path}")
        async def serve_spa(path: str):
            """Serve static files with SPA fallback."""
            file_path = static_dir / path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            # SPA fallback
            index_path = static_dir / "index.html"
            if index_path.exists():
                return FileResponse(index_path)
            return JSONResponse(
                content={"error": "Not found"},
                status_code=404,
            )

    return app


async def run_frontend_server(
    trace_store_url: str,
    host: str = "127.0.0.1",
    port: int = 3001,
    static_dir: Path | None = None,
) -> None:
    """Run the Frontend Server.

    Args:
        trace_store_url: URL to trace store (Jaeger)
        host: Host to bind to
        port: Port to listen on
        static_dir: Path to static files (React build output)
    """
    import uvicorn

    app = create_frontend_app(
        trace_store_url,
        static_dir=static_dir,
    )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


__all__ = [
    "FrontendServerState",
    "SpanBroadcaster",
    "WSMessageType",
    "create_frontend_app",
    "create_trace_reader",
    "get_server_state",
    "run_frontend_server",
]
