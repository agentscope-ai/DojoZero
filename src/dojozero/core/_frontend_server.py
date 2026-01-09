"""Frontend Server for DojoZero.

This module implements the Frontend Server which is responsible for:
- Reading traces from Trace Store (Jaeger or Dashboard)
- Pushing OTel spans to browsers via WebSocket
- Serving React static files (optional, for production)

Endpoints:
- GET  /api/traces                    - List all trials (from trace store)
- GET  /api/traces/{trial_id}         - Get complete trace data for replay
- WS   /ws/trials/{trial_id}/stream   - Real-time span stream

Configuration:
    # From Dashboard (local)
    dojo0 frontend --trace-store http://localhost:8000

    # From Jaeger (production)
    dojo0 frontend --trace-store http://jaeger:16686
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
    DashboardTraceReader,
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
    """Create appropriate TraceReader based on URL.

    Args:
        trace_store_url: URL to trace store
            - Dashboard: http://localhost:8000
            - Jaeger: http://localhost:16686
    """
    # Heuristic: Jaeger uses port 16686 by default
    if ":16686" in trace_store_url or "/jaeger" in trace_store_url.lower():
        LOGGER.info("Using Jaeger trace reader for %s", trace_store_url)
        return JaegerTraceReader(trace_store_url)
    else:
        LOGGER.info("Using Dashboard trace reader for %s", trace_store_url)
        return DashboardTraceReader(trace_store_url)


def create_frontend_app(
    trace_store_url: str,
    static_dir: Path | None = None,
    poll_interval: float = 1.0,
) -> FastAPI:
    """Create the Frontend Server FastAPI application.

    Args:
        trace_store_url: URL to trace store (Dashboard or Jaeger)
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
    # REST Endpoints for Trace Queries
    # -------------------------------------------------------------------------

    @app.get("/api/traces")
    async def list_traces() -> JSONResponse:
        """List all trials with traces."""
        state = get_server_state()
        trial_ids = await state.trace_reader.list_trials()
        result = [{"trial_id": tid} for tid in trial_ids]
        return JSONResponse(content=result)

    @app.get("/api/traces/{trial_id}")
    async def get_trial_trace(trial_id: str) -> JSONResponse:
        """Get complete trace data for a trial (for replay)."""
        state = get_server_state()
        spans = await state.trace_reader.get_spans(trial_id)
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

            # Track last seen span for incremental updates
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
                    # Poll for new spans
                    new_spans = await state.trace_reader.get_spans(
                        trial_id, since=last_time
                    )
                    for span in new_spans:
                        await state.broadcaster.broadcast_span(trial_id, span)

                    if new_spans:
                        last_us = max(s.start_time for s in new_spans)
                        last_time = datetime.fromtimestamp(
                            last_us / 1_000_000, tz=timezone.utc
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
        trace_store_url: URL to trace store (Dashboard or Jaeger)
        host: Host to bind to
        port: Port to listen on
        static_dir: Path to static files (React build output)
    """
    import uvicorn

    app = create_frontend_app(trace_store_url, static_dir=static_dir)

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
