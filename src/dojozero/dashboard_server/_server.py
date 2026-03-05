"""Dashboard Server for DojoZero.

This module implements the Dashboard Server which is responsible for:
- Running trials (agents, operators, data streams)
- Emitting OTel traces for all actor operations
- Providing REST API for trial control (submit, stop, checkpoint)
- Serving as trace store for Frontend Server
- Scheduling trials for future game events

Refactored from core/_dashboard_server.py to separate server code from core abstractions.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Coroutine

from fastapi import Depends, FastAPI, Query, Request

if TYPE_CHECKING:
    from dojozero.gateway import AgentAuthenticator
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from dojozero.core import (
    TrialOrchestrator,
    OrchestratorError,
    TrialExistsError,
    TrialNotFoundError,
    TrialSpec,
    TrialStatus,
)
from dojozero.core._registry import (
    TrialBuilderNotFoundError,
    get_trial_builder_definition,
)
from dojozero.core._tracing import (
    OTelSpanExporter,
    SLSLogExporter,
    get_sls_exporter_headers,
    get_sls_log_exporter,
    set_otel_exporter,
    set_sls_log_exporter,
)
from dojozero.core._types import RuntimeContext

from ._scheduler import SchedulerStore
from ._trial_manager import TrialManager
from ._types import InitialTrialSourceDict

LOGGER = logging.getLogger("dojozero.dashboard_server")


class ScenarioConfig(BaseModel):
    """Scenario configuration for trial submission."""

    name: str
    module: str | None = None
    config: dict[str, Any] = {}


class ResumeConfig(BaseModel):
    """Resume configuration for trial submission."""

    checkpoint_id: str | None = None
    latest: bool = False


class BacktestConfig(BaseModel):
    """Backtest configuration for trial submission.

    For security, file paths are not accepted via REST API.
    Use trial_id to reference a previous trial's event file.
    """

    trial_id: str  # Reference a previous trial to replay its events
    speed: float = 1.0
    max_sleep: float = 20.0
    emit_traces: bool = False  # Emit data events to SLS with rebased timestamps


# Backward compatibility alias (deprecated)
ReplayConfig = BacktestConfig


class TrialSubmitRequest(BaseModel):
    """Request body for submitting a new trial.

    Either 'scenario' or 'params' must be provided.
    When using 'params', the scenario is extracted from params['scenario'].
    """

    model_config = {"extra": "ignore"}

    trial_id: str | None = None
    scenario: ScenarioConfig | None = None
    params: dict[str, Any] | None = None  # Alternative: raw params payload
    metadata: dict[str, Any] | None = None
    resume: ResumeConfig | None = None
    backtest: BacktestConfig | None = None


class TrialSourceConfigRequest(BaseModel):
    """Configuration for a trial source."""

    scenario_name: str
    scenario_config: dict[str, Any] = {}
    pre_start_hours: float = 2.0
    check_interval_seconds: float = 60.0
    auto_stop_on_completion: bool = True
    data_dir: str | None = None


class TrialSourceRequest(BaseModel):
    """Request body for registering a trial source.

    Both NBA and NFL use ESPN scoreboard to discover games automatically.
    """

    model_config = {"extra": "ignore"}

    source_id: str
    sport_type: str  # "nba" or "nfl"
    config: TrialSourceConfigRequest


@dataclass
class DashboardServerState:
    """Shared state for the Dashboard Server."""

    orchestrator: TrialOrchestrator
    trial_manager: TrialManager
    schedule_manager: Any | None = None  # ScheduleManager, lazy import
    trace_backend: str | None = None
    oss_backup: bool = False
    data_dir: Path | None = None  # Base directory for trial data files
    imported_modules: set[str] = field(default_factory=set)
    import_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def get_server_state(request: Request) -> DashboardServerState:
    """Dependency to get server state from app.state."""
    state = getattr(request.app.state, "server_state", None)
    if state is None:
        raise RuntimeError("Server not initialized")
    return state


async def _launch_backtest_trial(
    orchestrator: TrialOrchestrator,
    spec: TrialSpec,
    event_file: Path,
    speed: float,
    max_sleep: float,
    emit_traces: bool = False,
) -> TrialStatus:
    """Launch a trial in backtest mode.

    This sets up the backtest infrastructure and launches the trial with
    events from the specified file.
    """

    from dojozero.data import BacktestCoordinator, DataHub

    builder_name = spec.metadata.get("builder_name")
    if not builder_name:
        raise OrchestratorError("builder_name is required in metadata for backtest")
    builder_name = str(builder_name)

    # Extract hub_id from spec (from stream configs)
    hub_id = None
    for stream_spec in spec.data_streams:
        config = stream_spec.config
        if config.get("hub_id"):
            hub_id = config["hub_id"]
            break

    if not hub_id:
        hub_id = str(spec.metadata.get("hub_id", "data_hub"))

    # Create DataHub in backtest mode (uses event_file path for consistency)
    hub = DataHub(
        hub_id=hub_id,
        persistence_file=str(event_file),
    )

    if emit_traces:
        hub.enable_backtest_traces(trial_id=spec.trial_id)

    # Create BacktestCoordinator
    coordinator = BacktestCoordinator(data_hub=hub, backtest_file=event_file)
    coordinator.set_speed(speed_up=speed, max_sleep=max_sleep)

    # Load events
    LOGGER.info("Loading events from file: %s", event_file)
    await coordinator.start()

    # Override context builder
    builder_def = get_trial_builder_definition(builder_name)
    original_context_builder = builder_def.context_builder

    def backtest_context_builder(spec: TrialSpec) -> RuntimeContext:
        return RuntimeContext(
            trial_id=spec.trial_id,
            data_hubs={hub_id: hub},
            stores={},
        )

    builder_def.context_builder = backtest_context_builder

    try:
        # Launch trial
        status = await orchestrator.launch_trial(spec)

        # Start backtest in background

        async def run_backtest():
            try:
                await coordinator.run_all()
                LOGGER.info("Backtest completed for trial '%s'", spec.trial_id)
            except Exception as e:
                LOGGER.error("Backtest failed: %s", e)
            finally:
                coordinator.stop()
                builder_def.context_builder = original_context_builder

        asyncio.create_task(run_backtest())
        return status
    except Exception:
        builder_def.context_builder = original_context_builder
        raise


def _get_sls_otlp_endpoint() -> str:
    """Construct SLS OTLP endpoint from environment variables."""
    project = os.environ.get("DOJOZERO_SLS_PROJECT", "")
    endpoint = os.environ.get("DOJOZERO_SLS_ENDPOINT", "")

    if not project:
        raise ValueError(
            "SLS backend requires DOJOZERO_SLS_PROJECT environment variable"
        )
    if not endpoint:
        raise ValueError(
            "SLS backend requires DOJOZERO_SLS_ENDPOINT environment variable"
        )

    return f"https://{project}.{endpoint}"


def create_dashboard_app(
    orchestrator: TrialOrchestrator,
    scheduler_store: SchedulerStore,
    trace_backend: str | None = None,
    trace_ingest_endpoint: str | None = None,
    oss_backup: bool = False,
    max_concurrent_trials: int = 20,
    service_name: str = "dojozero",
    initial_trial_sources: list[InitialTrialSourceDict] | None = None,
    auto_resume: bool = True,
    stale_threshold_hours: float = 24.0,
    enable_gateway: bool = False,
    data_dir: str | Path | None = None,
    authenticator: "AgentAuthenticator | None" = None,
) -> FastAPI:
    """Create the Dashboard Server FastAPI application.

    Args:
        orchestrator: TrialOrchestrator instance for trial management
        scheduler_store: SchedulerStore instance for schedule persistence
        trace_backend: Trace backend type ("jaeger" or "sls"), or None to disable tracing
        trace_ingest_endpoint: OTLP endpoint for Jaeger (only used when trace_backend="jaeger")
        oss_backup: Enable OSS backup for trial data when trials complete
        max_concurrent_trials: Maximum number of concurrent running trials (default 20)
        service_name: Service name for tracing
        initial_trial_sources: List of trial source configurations to register on startup
        auto_resume: Automatically resume interrupted trials on startup (default True)
        stale_threshold_hours: Skip resuming trials older than this many hours (default 24)
        enable_gateway: Enable HTTP gateway routing for external agents
        data_dir: Base directory for trial data files (persistence_file will be generated
            under this directory). If None, trials must provide their own persistence_file.
        authenticator: AgentAuthenticator for validating agent API keys. If None and
            agent_keys.yaml exists, uses LocalAgentAuthenticator. Otherwise NoOpAuthenticator.

    For SLS backend, configuration comes from environment variables:
        DOJOZERO_SLS_PROJECT: SLS project name
        DOJOZERO_SLS_ENDPOINT: SLS endpoint (e.g., cn-hangzhou.log.aliyuncs.com)
        DOJOZERO_SLS_LOGSTORE: Logstore name (e.g., "dojozero-traces")
    """

    # Create gateway router early so it can be passed to TrialManager
    gateway_router = None
    if enable_gateway:
        from ._gateway_routing import GatewayRouter

        gateway_router = GatewayRouter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Create trial manager with gateway router if enabled
        trial_manager = TrialManager(
            orchestrator=orchestrator,
            max_concurrent=max_concurrent_trials,
            oss_backup=oss_backup,
            auto_resume=auto_resume,
            stale_threshold_hours=stale_threshold_hours,
            gateway_router=gateway_router,
            authenticator=authenticator,
        )

        # Create schedule manager
        from ._scheduler import ScheduleManager

        schedule_manager = ScheduleManager(
            trial_manager=trial_manager,
            store=scheduler_store,
        )

        # Store state on app.state instead of global variable
        app.state.server_state = DashboardServerState(
            orchestrator=orchestrator,
            trial_manager=trial_manager,
            schedule_manager=schedule_manager,
            trace_backend=trace_backend,
            oss_backup=oss_backup,
            data_dir=Path(data_dir).resolve() if data_dir else None,
        )

        # Start trial manager worker
        await trial_manager.start()

        # Start schedule manager
        await schedule_manager.start()
        LOGGER.info("ScheduleManager started")

        # Register initial trial sources if provided
        if initial_trial_sources:
            from ._scheduler import TrialSourceConfig

            for source_data in initial_trial_sources:
                source_id = source_data["source_id"]
                sport_type = source_data["sport_type"]

                # Skip if already registered (from persistence)
                if schedule_manager.get_source(source_id) is not None:
                    LOGGER.info(
                        "Trial source '%s' already registered, skipping", source_id
                    )
                    continue

                # Convert config
                config_data = source_data.get("config", {})
                config = TrialSourceConfig(
                    scenario_name=config_data.get("scenario_name", ""),
                    scenario_config=config_data.get("scenario_config", {}),
                    pre_start_hours=config_data.get("pre_start_hours", 2.0),
                    check_interval_seconds=config_data.get(
                        "check_interval_seconds", 60.0
                    ),
                    auto_stop_on_completion=config_data.get(
                        "auto_stop_on_completion", True
                    ),
                    data_dir=config_data.get("data_dir"),
                    sync_interval_seconds=config_data.get(
                        "sync_interval_seconds", 300.0
                    ),
                )

                try:
                    schedule_manager.register_source(
                        source_id=source_id,
                        sport_type=sport_type,
                        config=config,
                    )
                    LOGGER.info(
                        "Registered initial trial source '%s' for %s",
                        source_id,
                        sport_type,
                    )
                except ValueError as e:
                    LOGGER.warning(
                        "Failed to register trial source '%s': %s",
                        source_id,
                        e,
                    )

        # Initialize OTel exporter based on backend
        otel_exporter = None
        if trace_backend == "sls":
            # SLS backend: construct endpoint from env vars
            otlp_endpoint = _get_sls_otlp_endpoint()
            headers = get_sls_exporter_headers()
            if headers:
                LOGGER.info("SLS authentication headers configured")
            else:
                LOGGER.warning(
                    "SLS backend selected but credentials not configured. "
                    "Configure via: 1) Environment variables (ALIBABA_CLOUD_ACCESS_KEY_ID), "
                    "2) ~/.alibabacloud/credentials file, or 3) ECS RAM role."
                )

            otel_exporter = OTelSpanExporter(otlp_endpoint, headers=headers)
            otel_exporter.start()
            set_otel_exporter(otel_exporter)
            LOGGER.info("OTel exporter configured: %s (backend: sls)", otlp_endpoint)

            # Also initialize SLS Log exporter for flat field indexing
            sls_project = os.environ.get("DOJOZERO_SLS_PROJECT", "")
            sls_endpoint = os.environ.get("DOJOZERO_SLS_ENDPOINT", "")
            sls_logstore = os.environ.get("DOJOZERO_SLS_LOGSTORE", "")
            if sls_project and sls_endpoint and sls_logstore:
                sls_log_exporter = SLSLogExporter(
                    project=sls_project,
                    endpoint=sls_endpoint,
                    logstore=sls_logstore,
                    service_name=service_name,
                )
                sls_log_exporter.start()
                set_sls_log_exporter(sls_log_exporter)
                LOGGER.info(
                    "SLS Log exporter configured: %s/%s (flat fields)",
                    sls_project,
                    sls_logstore,
                )

        elif trace_backend == "jaeger":
            # Jaeger or SLS backend: use provided endpoint or default
            otlp_endpoint = trace_ingest_endpoint or "http://localhost:4318"
            otel_exporter = OTelSpanExporter(
                otlp_endpoint, service_name=service_name, headers=None
            )
            otel_exporter.start()
            set_otel_exporter(otel_exporter)
            LOGGER.info(
                "OTel exporter configured: %s (backend: jaeger or sls, service_name: %s)",
                otlp_endpoint,
                service_name,
            )

        LOGGER.info(
            "Dashboard Server started (max_concurrent_trials=%d)",
            max_concurrent_trials,
        )
        yield

        # Graceful shutdown: stop schedule manager first
        if schedule_manager is not None:
            LOGGER.info("Dashboard Server shutting down - stopping schedule manager")
            try:
                await schedule_manager.stop()
            except Exception as e:
                LOGGER.error("Error stopping schedule manager: %s", e)

        # Stop trial manager (handles all running trials)
        LOGGER.info("Dashboard Server shutting down - stopping trial manager")
        try:
            await trial_manager.stop()
        except Exception as e:
            LOGGER.error("Error stopping trial manager: %s", e)

        # Also stop any trials that might be running directly in orchestrator
        try:
            running_trials = [
                status
                for status in orchestrator.list_trials()
                if status.phase.value in ("running", "starting")
            ]
            for trial_status in running_trials:
                try:
                    LOGGER.info(
                        "Stopping trial '%s' due to server shutdown",
                        trial_status.trial_id,
                    )
                    await orchestrator.stop_trial(trial_status.trial_id)
                except Exception as e:
                    LOGGER.warning(
                        "Failed to stop trial '%s': %s", trial_status.trial_id, e
                    )
                    # Still emit a terminated span even if stop fails
                    orchestrator._emit_trial_lifecycle_span(
                        trial_id=trial_status.trial_id,
                        phase="terminated",
                        metadata={"reason": "server_shutdown", "error": str(e)},
                    )
        except Exception as e:
            LOGGER.error("Error during trial cleanup: %s", e)

        # Shutdown exporters
        if otel_exporter is not None:
            otel_exporter.shutdown()
            set_otel_exporter(None)

        sls_log_exp = get_sls_log_exporter()
        if sls_log_exp is not None:
            sls_log_exp.shutdown()
            set_sls_log_exporter(None)

        LOGGER.info("Dashboard Server shutdown complete")

    app = FastAPI(
        title="DojoZero Dashboard Server",
        description="REST API for trial management, scheduling, and trace collection",
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
    async def list_trials(
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """List all known trials with their status.

        Includes both:
        - Queued trials (pending/starting) from TrialManager
        - Active/completed trials from Dashboard
        """
        result = []
        seen_ids: set[str] = set()

        # First, add trials from TrialManager (includes pending/queued)
        for queued in state.trial_manager.list_trials():
            trial_info = {
                "id": queued.trial_id,
                "phase": queued.phase.value,
                "metadata": asdict(queued.spec.metadata),
                "error": queued.error,
                "source": "queue",
            }
            result.append(trial_info)
            seen_ids.add(queued.trial_id)

        # Then add trials from Dashboard (active/historical)
        for trial_status in state.orchestrator.list_trials():
            if trial_status.trial_id in seen_ids:
                continue  # Already included from queue
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
                "source": "dashboard",
            }
            result.append(trial_info)

        return JSONResponse(content=result)

    @app.post("/api/trials")
    async def submit_trial(
        request: TrialSubmitRequest,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Submit a new trial to the dashboard server.

        This endpoint accepts trial specifications via JSON and launches them
        on the server. The trial_id is optional - if not provided, a UUID
        will be generated.

        Request body (option 1 - scenario):
        {
            "trial_id": "optional-trial-id",
            "scenario": {
                "name": "builder_name",
                "module": "optional.module.to.import",
                "config": {...}
            },
            "metadata": {...},
            "resume": {"checkpoint_id": "...", "latest": false},
            "backtest": {"trial_id": "source-trial-id", "speed": 1.0, "max_sleep": 20.0}
        }

        Request body (option 2 - params):
        {
            "trial_id": "optional-trial-id",
            "params": {"scenario": {"name": "..."}, ...},
            "resume": {...},
            "backtest": {...}
        }
        """
        import importlib
        from uuid import uuid4

        # Generate trial_id if not provided
        trial_id = request.trial_id or uuid4().hex

        # Determine scenario source: direct scenario or from params
        scenario = request.scenario
        scenario_config = {}

        # Initialize metadata dict if None
        metadata = request.metadata or {}

        if request.params:
            # Extract scenario from params payload
            params_scenario = request.params.get("scenario", {})
            if isinstance(params_scenario, dict):
                scenario_name = params_scenario.get("name")
                scenario_module = params_scenario.get("module")
                scenario_config = params_scenario.get("config", {})
                if scenario_name:
                    scenario = ScenarioConfig(
                        name=scenario_name,
                        module=scenario_module,
                        config=scenario_config,
                    )
            # Extract metadata from params if not provided directly
            if not metadata and "metadata" in request.params:
                params_metadata = request.params.get("metadata", {})
                if isinstance(params_metadata, dict):
                    metadata.update(params_metadata)

        if not scenario:
            return JSONResponse(
                content={"error": "Either 'scenario' or 'params.scenario' is required"},
                status_code=400,
            )

        # Import module if specified and not already imported (with lock for thread safety)
        if scenario.module:
            module_name = scenario.module
            async with state.import_lock:
                if module_name not in state.imported_modules:
                    try:
                        importlib.import_module(module_name)
                        state.imported_modules.add(module_name)
                        LOGGER.info("Imported module: %s", module_name)
                    except ImportError as e:
                        return JSONResponse(
                            content={
                                "error": f"Failed to import module '{module_name}': {e}"
                            },
                            status_code=400,
                        )

        # Get builder definition
        try:
            definition = get_trial_builder_definition(scenario.name)
        except TrialBuilderNotFoundError as e:
            return JSONResponse(
                content={"error": str(e)},
                status_code=400,
            )

        # Prepare config with server-generated persistence_file for security
        # User-provided persistence_file paths are NOT trusted (path traversal risk)
        builder_config = dict(scenario.config or scenario_config)
        if state.data_dir:
            # Generate safe persistence_file path based on trial_id
            from datetime import date

            date_str = date.today().isoformat()
            safe_persistence_file = (
                state.data_dir / scenario.name / date_str / f"{trial_id}.jsonl"
            )
            safe_persistence_file.parent.mkdir(parents=True, exist_ok=True)

            # Inject into hub config (override any user-provided value)
            if "hub" not in builder_config:
                builder_config["hub"] = {}
            builder_config["hub"]["persistence_file"] = str(safe_persistence_file)
            LOGGER.debug(
                "Generated persistence_file for trial %s: %s",
                trial_id,
                safe_persistence_file,
            )

        # Build the trial spec - uses build_async which handles both sync and async builders
        try:
            spec = await definition.build_async(trial_id, builder_config)
        except ValidationError as e:
            return JSONResponse(
                content={"error": f"Invalid config for builder '{scenario.name}': {e}"},
                status_code=400,
            )

        # Add metadata
        if metadata:
            spec.metadata.update(metadata)

        # Handle resume configuration
        if request.resume:
            if request.resume.checkpoint_id:
                spec.resume_from_checkpoint_id = request.resume.checkpoint_id
            elif request.resume.latest:
                spec.resume_from_latest = True

        # Handle backtest configuration
        launch_coro_factory = None
        if request.backtest:
            # Look up the source trial's persistence file
            source_trial_id = request.backtest.trial_id
            source_record = state.orchestrator.store.get_trial_record(source_trial_id)
            if source_record is None:
                return JSONResponse(
                    content={"error": f"Source trial not found: {source_trial_id}"},
                    status_code=404,
                )

            # Get persistence_file from source trial's metadata
            source_persistence_file = source_record.spec.metadata.get(
                "persistence_file"
            )
            if not source_persistence_file:
                return JSONResponse(
                    content={
                        "error": f"Source trial '{source_trial_id}' has no persistence_file"
                    },
                    status_code=400,
                )

            event_file = Path(source_persistence_file)
            if not event_file.exists():
                return JSONResponse(
                    content={
                        "error": f"Event file not found for trial '{source_trial_id}': {event_file}"
                    },
                    status_code=400,
                )
            # Capture backtest settings (for closure below)
            backtest_speed = request.backtest.speed
            backtest_max_sleep = request.backtest.max_sleep
            backtest_emit_traces = request.backtest.emit_traces

            # Convert metadata to backtest-specific type with required backtest fields
            from dataclasses import asdict

            from dojozero.betting import BacktestBettingTrialMetadata

            metadata_dict = asdict(spec.metadata)
            spec.metadata = BacktestBettingTrialMetadata(
                **metadata_dict,
                backtest_mode=True,
                backtest_file=str(event_file),
                backtest_speed=backtest_speed,
                backtest_max_sleep=backtest_max_sleep,
            )
            spec.builder_name = scenario.name

            # Create factory for backtest launch
            def make_backtest_coro() -> Coroutine[Any, Any, TrialStatus]:
                return _launch_backtest_trial(
                    orchestrator=state.orchestrator,
                    spec=spec,
                    event_file=event_file,
                    speed=backtest_speed,
                    max_sleep=backtest_max_sleep,
                    emit_traces=backtest_emit_traces,
                )

            launch_coro_factory = make_backtest_coro

        # Queue the trial for execution (returns immediately)
        try:
            trial_id = await state.trial_manager.submit(
                spec=spec,
                launch_coro_factory=launch_coro_factory,
            )
        except TrialExistsError as e:
            return JSONResponse(
                content={"error": str(e)},
                status_code=409,
            )
        except Exception as e:
            LOGGER.error("Failed to queue trial: %s", e, exc_info=True)
            return JSONResponse(
                content={"error": f"Failed to queue trial: {e}"},
                status_code=500,
            )

        # Return immediately with pending status
        return JSONResponse(
            content={
                "id": trial_id,
                "phase": "pending",
                "message": "Trial queued for execution",
                "queue_position": state.trial_manager.pending_count,
                "running_count": state.trial_manager.running_count,
            },
            status_code=202,  # 202 Accepted (queued for processing)
        )

    @app.get("/api/trials/{trial_id}/status")
    async def get_trial_status(
        trial_id: str,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Get status for a specific trial.

        Checks both TrialManager (for queued trials) and Dashboard (for active trials).
        """
        # First check TrialManager for queued/pending trials
        queued = state.trial_manager.get_status(trial_id)
        if queued is not None:
            return JSONResponse(
                content={
                    "id": queued.trial_id,
                    "phase": queued.phase.value,
                    "metadata": asdict(queued.spec.metadata),
                    "error": queued.error,
                    "source": "queue",
                }
            )

        # Fall back to Dashboard for active/completed trials
        try:
            status = state.orchestrator.get_trial_status(trial_id)
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
                "source": "dashboard",
            }
        )

    @app.get("/api/trials/{trial_id}/results")
    async def get_trial_results(
        request: Request,
        trial_id: str,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Get results for a trial (live or concluded).

        This unified endpoint works for both running and concluded trials:
        - Running trials: fetches live results from the gateway adapter
        - Concluded trials: returns persisted results from storage

        Returns 404 if trial not found or results not available.
        """
        # Try live gateway first (for running trials)
        gateway_router = getattr(request.app.state, "gateway_router", None)
        if gateway_router is not None:
            gateway_state = gateway_router.get_gateway_state(trial_id)
            if gateway_state is not None:
                try:
                    results = await gateway_state.adapter.get_results()
                    return JSONResponse(
                        content=results.model_dump(mode="json", by_alias=True)
                    )
                except Exception as e:
                    LOGGER.warning(
                        "Failed to get live results for trial '%s': %s",
                        trial_id,
                        e,
                    )

        # Fall back to persisted results (for concluded trials)
        results = state.orchestrator.store.get_trial_results(trial_id)
        if results is not None:
            return JSONResponse(content=results)

        # No results available
        return JSONResponse(
            content={
                "error": f"Results not found for trial '{trial_id}'",
                "hint": "Trial may not exist or has not concluded yet.",
            },
            status_code=404,
        )

    @app.post("/api/trials/{trial_id}/stop")
    async def stop_trial(
        trial_id: str,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Stop a running trial."""
        try:
            status = await state.orchestrator.stop_trial(trial_id)
        except TrialNotFoundError:
            return JSONResponse(
                content={"error": f"Trial '{trial_id}' not found"},
                status_code=404,
            )

        # OSS backup if enabled
        oss_uploaded = False
        if state.oss_backup:
            from ._trial_manager import upload_trial_to_oss

            # Get persistence_file from trial metadata
            try:
                trial_status = state.orchestrator.get_trial_status(trial_id)
                persistence_file_path = trial_status.metadata.get("persistence_file")
                if persistence_file_path and isinstance(persistence_file_path, str):
                    persistence_file = Path(persistence_file_path)
                    oss_uploaded = upload_trial_to_oss(trial_id, persistence_file)
                else:
                    LOGGER.warning(
                        "OSS backup enabled but no persistence_file in trial metadata"
                    )
            except Exception as e:
                LOGGER.error("Failed to upload trial %s to OSS: %s", trial_id, e)

        return JSONResponse(
            content={
                "id": status.trial_id,
                "phase": status.phase.value,
                "oss_uploaded": oss_uploaded,
            }
        )

    @app.delete("/api/trials/{trial_id}")
    async def cancel_trial(
        trial_id: str,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Cancel a pending or running trial.

        This cancels trials in the queue (pending) or stops running trials.
        Already completed/failed trials cannot be cancelled.
        """
        # Try to cancel via TrialManager first (handles queued trials)
        cancelled = await state.trial_manager.cancel(trial_id)
        if cancelled:
            return JSONResponse(
                content={
                    "id": trial_id,
                    "phase": "cancelled",
                    "message": "Trial cancelled successfully",
                }
            )

        # Fall back to stopping via Dashboard (for trials not in queue)
        try:
            status = await state.orchestrator.stop_trial(trial_id)
            return JSONResponse(
                content={
                    "id": status.trial_id,
                    "phase": status.phase.value,
                    "message": "Trial stopped",
                }
            )
        except TrialNotFoundError:
            return JSONResponse(
                content={"error": f"Trial '{trial_id}' not found"},
                status_code=404,
            )

    # -------------------------------------------------------------------------
    # Game Discovery Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/games/nba")
    async def list_nba_games(
        date: str | None = Query(None, description="Date in YYYY-MM-DD format"),
        start_date: str | None = Query(None, description="Start date for range"),
        end_date: str | None = Query(None, description="End date for range"),
    ) -> JSONResponse:
        """List NBA games for a date or date range."""
        from ._game_discovery import NBAGameFetcher

        fetcher = NBAGameFetcher()
        try:
            if start_date and end_date:
                # Date range query
                games = await fetcher.fetch_games_for_date_range(start_date, end_date)
                return JSONResponse(
                    content={
                        "start_date": start_date,
                        "end_date": end_date,
                        "games": [g.to_dict() for g in games],
                    }
                )
            else:
                # Single date query
                games = await fetcher.fetch_games_for_date(date)
                return JSONResponse(
                    content={
                        "date": date or "today",
                        "games": [g.to_dict() for g in games],
                    }
                )
        except Exception as e:
            LOGGER.error("Error fetching NBA games: %s", e)
            return JSONResponse(
                content={"error": str(e)},
                status_code=500,
            )

    @app.get("/api/games/nfl")
    async def list_nfl_games(
        date: str | None = Query(None, description="Date in YYYY-MM-DD format"),
        week: int | None = Query(None, description="NFL week number (1-18)"),
    ) -> JSONResponse:
        """List NFL games for a date or week."""
        from ._game_discovery import NFLGameFetcher

        fetcher = NFLGameFetcher()
        try:
            if week is not None:
                games = await fetcher.fetch_games_for_week(week)
                return JSONResponse(
                    content={
                        "week": week,
                        "games": [g.to_dict() for g in games],
                    }
                )
            else:
                games = await fetcher.fetch_games_for_date(date)
                return JSONResponse(
                    content={
                        "date": date or "today",
                        "games": [g.to_dict() for g in games],
                    }
                )
        except Exception as e:
            LOGGER.error("Error fetching NFL games: %s", e)
            return JSONResponse(
                content={"error": str(e)},
                status_code=500,
            )

    # -------------------------------------------------------------------------
    # Trial Source Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/trial-sources")
    async def list_trial_sources(
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """List all registered trial sources."""
        if state.schedule_manager is None:
            return JSONResponse(
                content={
                    "error": "Scheduling not enabled. Configure filesystem store to enable."
                },
                status_code=400,
            )

        sources = state.schedule_manager.list_sources()
        return JSONResponse(
            content={
                "count": len(sources),
                "sources": [s.to_dict() for s in sources],
            }
        )

    @app.post("/api/trial-sources")
    async def register_trial_source(
        request: TrialSourceRequest,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Register a new trial source for automatic scheduling."""
        if state.schedule_manager is None:
            return JSONResponse(
                content={
                    "error": "Scheduling not enabled. Configure filesystem store to enable."
                },
                status_code=400,
            )

        from ._scheduler import TrialSourceConfig

        try:
            # Convert request to config
            config = TrialSourceConfig(
                scenario_name=request.config.scenario_name,
                scenario_config=request.config.scenario_config,
                pre_start_hours=request.config.pre_start_hours,
                check_interval_seconds=request.config.check_interval_seconds,
                auto_stop_on_completion=request.config.auto_stop_on_completion,
                data_dir=request.config.data_dir,
            )

            source = state.schedule_manager.register_source(
                source_id=request.source_id,
                sport_type=request.sport_type,
                config=config,
            )

            return JSONResponse(
                content=source.to_dict(),
                status_code=201,
            )
        except ValueError as e:
            return JSONResponse(
                content={"error": str(e)},
                status_code=400,
            )
        except Exception as e:
            LOGGER.error("Failed to register trial source: %s", e, exc_info=True)
            return JSONResponse(
                content={"error": f"Failed to register trial source: {e}"},
                status_code=500,
            )

    @app.get("/api/trial-sources/{source_id}")
    async def get_trial_source(
        source_id: str,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Get a specific trial source."""
        if state.schedule_manager is None:
            return JSONResponse(
                content={"error": "Scheduling not enabled"},
                status_code=400,
            )

        source = state.schedule_manager.get_source(source_id)
        if source is None:
            return JSONResponse(
                content={"error": f"Trial source '{source_id}' not found"},
                status_code=404,
            )

        return JSONResponse(content=source.to_dict())

    @app.delete("/api/trial-sources/{source_id}")
    async def unregister_trial_source(
        source_id: str,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Unregister a trial source."""
        if state.schedule_manager is None:
            return JSONResponse(
                content={"error": "Scheduling not enabled"},
                status_code=400,
            )

        removed = state.schedule_manager.unregister_source(source_id)
        if not removed:
            return JSONResponse(
                content={"error": f"Trial source '{source_id}' not found"},
                status_code=404,
            )

        return JSONResponse(
            content={
                "source_id": source_id,
                "message": "Trial source unregistered successfully",
            }
        )

    @app.post("/api/trial-sources/{source_id}/sync")
    async def sync_trial_source(
        source_id: str,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Manually trigger sync for a trial source."""
        if state.schedule_manager is None:
            return JSONResponse(
                content={"error": "Scheduling not enabled"},
                status_code=400,
            )

        try:
            scheduled = await state.schedule_manager.sync_source(source_id)
            return JSONResponse(
                content={
                    "source_id": source_id,
                    "scheduled_count": len(scheduled),
                    "scheduled_trials": [s.to_dict() for s in scheduled],
                }
            )
        except ValueError as e:
            return JSONResponse(
                content={"error": str(e)},
                status_code=404,
            )
        except Exception as e:
            LOGGER.error("Failed to sync trial source: %s", e, exc_info=True)
            return JSONResponse(
                content={"error": f"Failed to sync: {e}"},
                status_code=500,
            )

    @app.patch("/api/trial-sources/{source_id}")
    async def update_trial_source(
        source_id: str,
        enabled: bool = Query(..., description="Enable or disable the source"),
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Enable or disable a trial source."""
        if state.schedule_manager is None:
            return JSONResponse(
                content={"error": "Scheduling not enabled"},
                status_code=400,
            )

        updated = state.schedule_manager.set_source_enabled(source_id, enabled)
        if not updated:
            return JSONResponse(
                content={"error": f"Trial source '{source_id}' not found"},
                status_code=404,
            )

        source = state.schedule_manager.get_source(source_id)
        return JSONResponse(
            content=source.to_dict() if source else {"source_id": source_id}
        )

    # -------------------------------------------------------------------------
    # Scheduled Trials Endpoints (read-only view of auto-scheduled trials)
    # -------------------------------------------------------------------------

    @app.get("/api/scheduled-trials")
    async def list_scheduled_trials(
        include_finished: bool = False,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """List scheduled trials (auto-scheduled from trial sources).

        Args:
            include_finished: If true, include completed/cancelled/failed trials.
                             Default is false (only active trials).
        """
        if state.schedule_manager is None:
            return JSONResponse(
                content={
                    "error": "Scheduling not enabled. Configure filesystem store to enable."
                },
                status_code=400,
            )

        schedules = state.schedule_manager.list_scheduled(
            include_finished=include_finished
        )
        return JSONResponse(
            content={
                "count": len(schedules),
                "scheduled_trials": [s.to_dict() for s in schedules],
            }
        )

    @app.get("/api/scheduled-trials/{schedule_id}")
    async def get_scheduled_trial(
        schedule_id: str,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Get status for a specific scheduled trial."""
        if state.schedule_manager is None:
            return JSONResponse(
                content={"error": "Scheduling not enabled"},
                status_code=400,
            )

        scheduled = state.schedule_manager.get_scheduled(schedule_id)
        if scheduled is None:
            return JSONResponse(
                content={"error": f"Scheduled trial '{schedule_id}' not found"},
                status_code=404,
            )

        return JSONResponse(content=scheduled.to_dict())

    @app.delete("/api/scheduled-trials/{schedule_id}")
    async def cancel_scheduled_trial(
        schedule_id: str,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Cancel a scheduled trial."""
        if state.schedule_manager is None:
            return JSONResponse(
                content={"error": "Scheduling not enabled"},
                status_code=400,
            )

        cancelled = await state.schedule_manager.cancel_scheduled(schedule_id)
        if not cancelled:
            return JSONResponse(
                content={
                    "error": f"Scheduled trial '{schedule_id}' not found or already completed"
                },
                status_code=404,
            )

        return JSONResponse(
            content={
                "schedule_id": schedule_id,
                "phase": "cancelled",
                "message": "Scheduled trial cancelled successfully",
            }
        )

    @app.delete("/api/scheduled-trials")
    async def clear_all_scheduled_trials(
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Clear all scheduled trials."""
        if state.schedule_manager is None:
            return JSONResponse(
                content={"error": "Scheduling not enabled"},
                status_code=400,
            )

        count = await state.schedule_manager.clear_all_scheduled()
        return JSONResponse(
            content={
                "cleared_count": count,
                "message": f"Cleared {count} scheduled trial(s)",
            }
        )

    # -------------------------------------------------------------------------
    # Health Check
    # -------------------------------------------------------------------------

    @app.get("/health")
    async def health_check(
        state: DashboardServerState = Depends(get_server_state),
    ):
        """Health check endpoint."""
        return {
            "status": "ok",
            "trial_manager": {
                "pending": state.trial_manager.pending_count,
                "running": state.trial_manager.running_count,
            },
            "scheduling_enabled": state.schedule_manager is not None,
            "gateway_enabled": enable_gateway,
        }

    # -------------------------------------------------------------------------
    # Gateway Routing (for external agents)
    # -------------------------------------------------------------------------

    if enable_gateway and gateway_router is not None:
        from ._gateway_routing import create_gateway_routes

        gateway_routes_app = create_gateway_routes(gateway_router)

        # Mount the gateway routes
        app.mount("/", gateway_routes_app)

        # Store gateway router on app.state for reference
        app.state.gateway_router = gateway_router
        LOGGER.info("Gateway routing enabled at /api/gateway/{trial_id}/")

    return app


async def run_dashboard_server(
    orchestrator: TrialOrchestrator,
    scheduler_store: SchedulerStore,
    host: str = "127.0.0.1",
    port: int = 8000,
    trace_backend: str | None = None,
    trace_ingest_endpoint: str | None = None,
    oss_backup: bool = False,
    max_concurrent_trials: int = 20,
    service_name: str = "dojozero",
    initial_trial_sources: list[InitialTrialSourceDict] | None = None,
    auto_resume: bool = True,
    stale_threshold_hours: float = 24.0,
    enable_gateway: bool = False,
    data_dir: str | Path | None = None,
    authenticator: "AgentAuthenticator | None" = None,
) -> None:
    """Run the Dashboard Server.

    Args:
        orchestrator: TrialOrchestrator instance
        scheduler_store: SchedulerStore instance for schedule persistence
        host: Host to bind to
        port: Port to listen on
        trace_backend: Trace backend type ("jaeger" or "sls"), or None to disable tracing
        trace_ingest_endpoint: OTLP endpoint for Jaeger (only used when trace_backend="jaeger")
        oss_backup: Enable OSS backup for trial data when trials complete
        max_concurrent_trials: Maximum number of concurrent running trials (default 20)
        service_name: Service name for tracing
        initial_trial_sources: List of trial source configurations to register on startup
        auto_resume: Automatically resume interrupted trials on startup (default True)
        stale_threshold_hours: Skip resuming trials older than this many hours (default 24)
        enable_gateway: Enable HTTP gateway routing for external agents
        data_dir: Base directory for trial data files (persistence_file will be generated
            under this directory). If None, trials must provide their own persistence_file.
        authenticator: AgentAuthenticator for validating agent API keys
    """
    import uvicorn

    app = create_dashboard_app(
        orchestrator,
        scheduler_store=scheduler_store,
        trace_backend=trace_backend,
        trace_ingest_endpoint=trace_ingest_endpoint,
        oss_backup=oss_backup,
        max_concurrent_trials=max_concurrent_trials,
        service_name=service_name,
        initial_trial_sources=initial_trial_sources,
        auto_resume=auto_resume,
        stale_threshold_hours=stale_threshold_hours,
        enable_gateway=enable_gateway,
        data_dir=data_dir,
        authenticator=authenticator,
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
    "BacktestConfig",
    "DashboardServerState",
    "ReplayConfig",
    "ResumeConfig",
    "ScenarioConfig",
    "SchedulerStore",
    "TrialSourceConfigRequest",
    "TrialSourceRequest",
    "TrialSubmitRequest",
    "create_dashboard_app",
    "get_server_state",
    "run_dashboard_server",
]
