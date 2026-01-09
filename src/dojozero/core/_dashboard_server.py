"""Dashboard Server for DojoZero.

This module implements the Dashboard Server which is responsible for:
- Running trials (agents, operators, data streams)
- Emitting OTel traces for all actor operations
- Providing REST API for trial control (submit, stop, checkpoint)
- Serving as trace store for Frontend Server
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ._dashboard import Dashboard, TrialNotFoundError
from ._tracing import load_spans_from_checkpoint

LOGGER = logging.getLogger("dojozero.dashboard_server")


@dataclass
class DashboardServerState:
    """Shared state for the Dashboard Server."""

    dashboard: Dashboard
    otlp_endpoint: str | None = None


_server_state: DashboardServerState | None = None


def get_server_state() -> DashboardServerState:
    """Get the current server state."""
    if _server_state is None:
        raise RuntimeError("Server not initialized")
    return _server_state


def create_dashboard_app(
    dashboard: Dashboard,
    otlp_endpoint: str | None = None,
) -> FastAPI:
    """Create the Dashboard Server FastAPI application.

    Args:
        dashboard: Dashboard instance for trial management
        otlp_endpoint: OTLP endpoint URL for external trace storage.
                      If None, uses built-in DashboardStore for traces.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _server_state
        _server_state = DashboardServerState(
            dashboard=dashboard,
            otlp_endpoint=otlp_endpoint,
        )
        LOGGER.info("Dashboard Server started")
        yield
        LOGGER.info("Dashboard Server shutting down")

    app = FastAPI(
        title="DojoZero Dashboard Server",
        description="REST API for trial management and trace collection",
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
    # Trial Control Endpoints
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

    @app.get("/api/trials/{trial_id}/status")
    async def get_trial_status(trial_id: str) -> JSONResponse:
        """Get status for a specific trial."""
        state = get_server_state()
        try:
            status = state.dashboard.get_trial_status(trial_id)
        except TrialNotFoundError:
            return JSONResponse(
                content={"error": f"Trial '{trial_id}' not found"},
                status_code=404,
            )

        return JSONResponse(
            content={
                "id": status.trial_id,
                "phase": status.phase.value,
                "metadata": dict(status.metadata),
                "actors": [
                    {
                        "actor_id": actor.actor_id,
                        "role": actor.role.value,
                        "phase": actor.phase.value,
                        "last_error": actor.last_error,
                    }
                    for actor in status.actors
                ],
                "last_error": status.last_error,
            }
        )

    @app.post("/api/trials/{trial_id}/stop")
    async def stop_trial(trial_id: str) -> JSONResponse:
        """Stop a running trial."""
        state = get_server_state()
        try:
            status = await state.dashboard.stop_trial(trial_id)
        except TrialNotFoundError:
            return JSONResponse(
                content={"error": f"Trial '{trial_id}' not found"},
                status_code=404,
            )

        return JSONResponse(
            content={
                "id": status.trial_id,
                "phase": status.phase.value,
            }
        )

    # -------------------------------------------------------------------------
    # Trace Query API (for Frontend Server)
    # -------------------------------------------------------------------------

    @app.get("/api/traces")
    async def list_traces() -> JSONResponse:
        """List all trial IDs that have checkpoint data."""
        state = get_server_state()
        store = state.dashboard.store
        # List trials that have checkpoints
        trial_ids = []
        for record in store.list_trial_records():
            checkpoints = store.list_checkpoints(record.trial_id)
            if checkpoints:
                trial_ids.append(record.trial_id)
        result = [{"trial_id": tid} for tid in trial_ids]
        return JSONResponse(content=result)

    @app.get("/api/traces/{trial_id}")
    async def get_trial_trace(trial_id: str) -> JSONResponse:
        """Get trace data for a trial from checkpoint.

        Uses unified span protocol - all data flows through spans:
        - Resource spans (*.registered): Actor metadata
        - Event spans: Runtime events with business data

        No separate agent_states field - frontend extracts everything from spans.
        """
        state = get_server_state()
        store = state.dashboard.store

        # Get latest checkpoint
        summaries = store.list_checkpoints(trial_id)
        if not summaries:
            return JSONResponse(
                content={
                    "trial_id": trial_id,
                    "spans": [],
                }
            )

        latest = max(summaries, key=lambda s: s.created_at)
        checkpoint = store.load_checkpoint(latest.checkpoint_id)
        actor_states = dict(checkpoint.actor_states)

        # Convert to spans - includes registration spans and event spans
        spans = load_spans_from_checkpoint(trial_id, actor_states)

        return JSONResponse(
            content={
                "trial_id": trial_id,
                "spans": [span.to_dict() for span in spans],
            }
        )

    # -------------------------------------------------------------------------
    # Health Check
    # -------------------------------------------------------------------------

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok"}

    return app


async def run_dashboard_server(
    dashboard: Dashboard,
    host: str = "127.0.0.1",
    port: int = 8000,
    otlp_endpoint: str | None = None,
) -> None:
    """Run the Dashboard Server.

    Args:
        dashboard: Dashboard instance
        host: Host to bind to
        port: Port to listen on
        otlp_endpoint: OTLP endpoint for external trace storage (optional)
    """
    import uvicorn

    app = create_dashboard_app(dashboard, otlp_endpoint=otlp_endpoint)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


__all__ = [
    "DashboardServerState",
    "create_dashboard_app",
    "get_server_state",
    "run_dashboard_server",
]
