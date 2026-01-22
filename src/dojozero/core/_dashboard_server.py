"""Dashboard Server for DojoZero.

This module implements the Dashboard Server which is responsible for:
- Running trials (agents, operators, data streams)
- Emitting OTel traces for all actor operations
- Providing REST API for trial control (submit, stop, checkpoint)
- Serving as trace store for Frontend Server

The server uses an async TrialManager to queue and run trials:
- Submissions return immediately with trial_id
- Trials are queued and run up to max_concurrent (default 20)
- Status can be polled via GET /api/trials/{trial_id}/status
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from ._dashboard import (
    Dashboard,
    DashboardError,
    TrialExistsError,
    TrialNotFoundError,
    TrialSpec,
    TrialStatus,
)
from ._registry import (
    TrialBuilderNotFoundError,
    get_trial_builder_definition,
)
from ._tracing import (
    OTelSpanExporter,
    SLSLogExporter,
    get_sls_exporter_headers,
    set_otel_exporter,
    set_sls_log_exporter,
)
from ._types import RuntimeContext


class QueuedTrialPhase(str, Enum):
    """Phase of a queued trial in the TrialManager."""

    PENDING = "pending"  # In queue, waiting to start
    STARTING = "starting"  # Being launched
    RUNNING = "running"  # Active
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"  # Failed with error
    CANCELLED = "cancelled"  # Cancelled by user


@dataclass
class QueuedTrial:
    """A trial in the TrialManager queue."""

    trial_id: str
    spec: TrialSpec
    phase: QueuedTrialPhase = QueuedTrialPhase.PENDING
    error: str | None = None
    # Coroutine factory for launching (supports replay mode)
    launch_coro_factory: Callable[[], Coroutine[Any, Any, TrialStatus]] | None = None


class TrialManager:
    """Async task manager for running trials with queuing.

    Features:
    - Accepts trial submissions and returns immediately
    - Queues trials and runs up to max_concurrent in parallel
    - Tracks trial status (pending, running, completed, failed)
    - Supports cancellation of pending/running trials

    Usage:
        manager = TrialManager(dashboard, max_concurrent=20)
        await manager.start()  # Start background worker

        trial_id = await manager.submit(spec)  # Returns immediately
        status = manager.get_status(trial_id)  # Check status

        await manager.cancel(trial_id)  # Cancel if needed
        await manager.stop()  # Graceful shutdown
    """

    def __init__(
        self,
        dashboard: Dashboard,
        max_concurrent: int = 20,
        oss_backup: bool = False,
    ):
        """Initialize the TrialManager.

        Args:
            dashboard: Dashboard instance for launching trials
            max_concurrent: Maximum number of concurrent running trials
            oss_backup: Enable OSS backup when trials complete
        """
        self._dashboard = dashboard
        self._max_concurrent = max_concurrent
        self._oss_backup = oss_backup

        # Queue for pending trials
        self._pending: asyncio.Queue[QueuedTrial] = asyncio.Queue()

        # Track all trials by ID
        self._trials: dict[str, QueuedTrial] = {}

        # Track running tasks
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

        # Background worker task
        self._worker_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()

        self._logger = logging.getLogger("dojozero.trial_manager")

    async def start(self) -> None:
        """Start the background worker."""
        if self._worker_task is not None:
            return
        self._shutdown_event.clear()
        self._worker_task = asyncio.create_task(self._worker_loop())
        self._logger.info(
            "TrialManager started (max_concurrent=%d)", self._max_concurrent
        )

    async def stop(self) -> None:
        """Stop the manager and cancel all running trials."""
        self._logger.info("TrialManager stopping...")
        self._shutdown_event.set()

        # Cancel all running tasks
        for trial_id, task in list(self._running_tasks.items()):
            if not task.done():
                self._logger.info("Cancelling running trial: %s", trial_id)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Cancel worker
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        self._logger.info("TrialManager stopped")

    async def submit(
        self,
        spec: TrialSpec,
        launch_coro_factory: Callable[[], Coroutine[Any, Any, TrialStatus]] | None = None,
    ) -> str:
        """Submit a trial for execution.

        Args:
            spec: Trial specification
            launch_coro_factory: Optional custom launch coroutine factory
                                 (for replay mode). If None, uses dashboard.launch_trial.

        Returns:
            Trial ID

        Raises:
            TrialExistsError: If trial with this ID already exists
        """
        trial_id = spec.trial_id

        # Check for duplicate
        if trial_id in self._trials:
            raise TrialExistsError(f"Trial '{trial_id}' already exists")

        # Create queued trial
        queued = QueuedTrial(
            trial_id=trial_id,
            spec=spec,
            phase=QueuedTrialPhase.PENDING,
            launch_coro_factory=launch_coro_factory,
        )
        self._trials[trial_id] = queued

        # Add to queue
        await self._pending.put(queued)
        self._logger.info(
            "Trial '%s' queued (queue_size=%d, running=%d)",
            trial_id,
            self._pending.qsize(),
            len(self._running_tasks),
        )

        return trial_id

    def get_status(self, trial_id: str) -> QueuedTrial | None:
        """Get status of a queued trial.

        Args:
            trial_id: Trial identifier

        Returns:
            QueuedTrial or None if not found
        """
        return self._trials.get(trial_id)

    def list_trials(self) -> list[QueuedTrial]:
        """List all trials tracked by the manager."""
        return list(self._trials.values())

    async def cancel(self, trial_id: str) -> bool:
        """Cancel a pending or running trial.

        Args:
            trial_id: Trial identifier

        Returns:
            True if cancelled, False if not found or already completed
        """
        queued = self._trials.get(trial_id)
        if queued is None:
            return False

        if queued.phase == QueuedTrialPhase.PENDING:
            # Mark as cancelled (will be skipped by worker)
            queued.phase = QueuedTrialPhase.CANCELLED
            self._logger.info("Cancelled pending trial: %s", trial_id)
            return True

        if queued.phase in (QueuedTrialPhase.STARTING, QueuedTrialPhase.RUNNING):
            # Cancel running task
            task = self._running_tasks.get(trial_id)
            if task and not task.done():
                task.cancel()
                queued.phase = QueuedTrialPhase.CANCELLED
                self._logger.info("Cancelled running trial: %s", trial_id)
                # Also stop via dashboard
                try:
                    await self._dashboard.stop_trial(trial_id)
                except Exception as e:
                    self._logger.warning("Error stopping trial %s: %s", trial_id, e)
                return True

        return False

    @property
    def pending_count(self) -> int:
        """Number of pending trials in queue."""
        return self._pending.qsize()

    @property
    def running_count(self) -> int:
        """Number of currently running trials."""
        return len(self._running_tasks)

    async def _worker_loop(self) -> None:
        """Background worker that processes the queue."""
        while not self._shutdown_event.is_set():
            try:
                # Wait for a slot to be available
                while len(self._running_tasks) >= self._max_concurrent:
                    # Clean up completed tasks
                    self._cleanup_completed_tasks()
                    if len(self._running_tasks) >= self._max_concurrent:
                        await asyncio.sleep(0.5)
                    if self._shutdown_event.is_set():
                        return

                # Get next trial from queue (with timeout to check shutdown)
                try:
                    queued = await asyncio.wait_for(
                        self._pending.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Skip cancelled trials
                if queued.phase == QueuedTrialPhase.CANCELLED:
                    self._logger.debug("Skipping cancelled trial: %s", queued.trial_id)
                    continue

                # Launch trial in background task
                task = asyncio.create_task(self._run_trial(queued))
                self._running_tasks[queued.trial_id] = task

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error("Worker error: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

    def _cleanup_completed_tasks(self) -> None:
        """Remove completed tasks from running dict."""
        completed = [
            trial_id
            for trial_id, task in self._running_tasks.items()
            if task.done()
        ]
        for trial_id in completed:
            del self._running_tasks[trial_id]

    async def _run_trial(self, queued: QueuedTrial) -> None:
        """Run a single trial."""
        trial_id = queued.trial_id
        self._logger.info("Starting trial: %s", trial_id)
        queued.phase = QueuedTrialPhase.STARTING

        try:
            # Launch via custom factory or default
            if queued.launch_coro_factory:
                await queued.launch_coro_factory()
            else:
                await self._dashboard.launch_trial(queued.spec)

            queued.phase = QueuedTrialPhase.RUNNING
            self._logger.info("Trial '%s' is now running", trial_id)

            # Wait for trial to complete by monitoring dashboard status
            while True:
                await asyncio.sleep(2.0)
                try:
                    status = self._dashboard.get_trial_status(trial_id)
                    if status.phase.value in ("completed", "stopped", "failed"):
                        break
                except TrialNotFoundError:
                    # Trial removed from dashboard
                    break

            # Check final status
            try:
                status = self._dashboard.get_trial_status(trial_id)
                if status.phase.value == "failed":
                    queued.phase = QueuedTrialPhase.FAILED
                    queued.error = status.last_error
                else:
                    queued.phase = QueuedTrialPhase.COMPLETED
            except TrialNotFoundError:
                queued.phase = QueuedTrialPhase.COMPLETED

            # OSS backup if enabled
            if self._oss_backup and queued.phase == QueuedTrialPhase.COMPLETED:
                self._upload_to_oss(trial_id, queued.spec)

            self._logger.info(
                "Trial '%s' finished with phase: %s", trial_id, queued.phase.value
            )

        except asyncio.CancelledError:
            queued.phase = QueuedTrialPhase.CANCELLED
            self._logger.info("Trial '%s' was cancelled", trial_id)
            raise
        except Exception as e:
            queued.phase = QueuedTrialPhase.FAILED
            queued.error = str(e)
            self._logger.error("Trial '%s' failed: %s", trial_id, e, exc_info=True)

    def _upload_to_oss(self, trial_id: str, spec: TrialSpec) -> None:
        """Upload trial data to OSS if configured."""
        persistence_file_path = spec.metadata.get("persistence_file")
        if persistence_file_path and isinstance(persistence_file_path, str):
            persistence_file = Path(persistence_file_path)
            _upload_trial_to_oss(trial_id, persistence_file)

# Lazy import for OSS to avoid import errors if oss2 not installed
_oss_client = None


def _upload_trial_to_oss(trial_id: str, persistence_file: Path | None) -> bool:
    """Upload trial data to OSS.

    Args:
        trial_id: Trial identifier
        persistence_file: Path to the persistence JSONL file

    Returns:
        True if upload succeeded, False otherwise
    """
    global _oss_client

    if not persistence_file or not persistence_file.exists():
        LOGGER.warning("No persistence file to upload for trial %s", trial_id)
        return False

    try:
        from dojozero.utils.oss import OSSClient

        if _oss_client is None:
            _oss_client = OSSClient.from_env()

        # Upload with key: trials/{trial_id}/events.jsonl
        oss_key = f"trials/{trial_id}/events.jsonl"
        full_key = _oss_client.upload_file(persistence_file, oss_key)
        LOGGER.info("Uploaded trial data to OSS: %s", full_key)
        return True

    except ImportError:
        LOGGER.warning("OSS backup requested but oss2 package not installed")
        return False
    except ValueError as e:
        LOGGER.warning("OSS backup failed - configuration error: %s", e)
        return False
    except Exception as e:
        LOGGER.error("OSS backup failed for trial %s: %s", trial_id, e)
        return False


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


class ReplayConfig(BaseModel):
    """Replay configuration for trial submission."""

    file: str  # Path to replay file (must be accessible on server)
    speed_up: float = 1.0
    max_sleep: float = 20.0


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
    replay: ReplayConfig | None = None


@dataclass
class DashboardServerState:
    """Shared state for the Dashboard Server."""

    dashboard: Dashboard
    trial_manager: TrialManager
    trace_backend: str | None = None
    oss_backup: bool = False
    imported_modules: set[str] = field(default_factory=set)
    import_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def get_server_state(request: Request) -> DashboardServerState:
    """Dependency to get server state from app.state."""
    state = getattr(request.app.state, "server_state", None)
    if state is None:
        raise RuntimeError("Server not initialized")
    return state


async def _launch_replay_trial(
    dashboard: Dashboard,
    spec: TrialSpec,
    replay_file: Path,
    speed_up: float,
    max_sleep: float,
) -> TrialStatus:
    """Launch a trial in replay mode.

    This sets up the replay infrastructure and launches the trial with
    events replayed from the specified file.
    """

    from dojozero.data import DataHub, ReplayCoordinator

    builder_name = spec.metadata.get("builder_name")
    if not builder_name:
        raise DashboardError("builder_name is required in metadata for replay")
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

    # Create DataHub in replay mode
    hub = DataHub(
        hub_id=hub_id,
        persistence_file=None,
        enable_persistence=False,
    )

    # Create ReplayCoordinator
    coordinator = ReplayCoordinator(data_hub=hub, replay_file=replay_file)
    coordinator.set_speed(speed_up=speed_up, max_sleep=max_sleep)

    # Load events
    LOGGER.info("Loading events from replay file: %s", replay_file)
    await coordinator.start_replay()

    # Override context builder
    builder_def = get_trial_builder_definition(builder_name)
    original_context_builder = builder_def.context_builder

    def replay_context_builder(spec: TrialSpec) -> RuntimeContext:
        return RuntimeContext(
            trial_id=spec.trial_id,
            data_hubs={hub_id: hub},
            stores={},
        )

    builder_def.context_builder = replay_context_builder

    try:
        # Launch trial
        status = await dashboard.launch_trial(spec)

        # Start replay in background
        import asyncio

        async def run_replay():
            try:
                await coordinator.replay_all()
                LOGGER.info("Replay completed for trial '%s'", spec.trial_id)
            except Exception as e:
                LOGGER.error("Replay failed: %s", e)
            finally:
                coordinator.stop_replay()
                builder_def.context_builder = original_context_builder

        asyncio.create_task(run_replay())
        return status
    except Exception:
        builder_def.context_builder = original_context_builder
        raise


def _get_sls_otlp_endpoint() -> str:
    """Construct SLS OTLP endpoint from environment variables."""
    import os

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
    dashboard: Dashboard,
    trace_backend: str | None = None,
    trace_ingest_endpoint: str | None = None,
    oss_backup: bool = False,
    max_concurrent_trials: int = 20,
) -> FastAPI:
    """Create the Dashboard Server FastAPI application.

    Args:
        dashboard: Dashboard instance for trial management
        trace_backend: Trace backend type ("jaeger" or "sls"), or None to disable tracing
        trace_ingest_endpoint: OTLP endpoint for Jaeger (only used when trace_backend="jaeger")
        oss_backup: Enable OSS backup for trial data when trials complete
        max_concurrent_trials: Maximum number of concurrent running trials (default 20)

    For SLS backend, configuration comes from environment variables:
        DOJOZERO_SLS_PROJECT: SLS project name
        DOJOZERO_SLS_ENDPOINT: SLS endpoint (e.g., cn-hangzhou.log.aliyuncs.com)
        DOJOZERO_SLS_LOGSTORE: Logstore name (e.g., "dojozero-traces")
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Create trial manager
        trial_manager = TrialManager(
            dashboard=dashboard,
            max_concurrent=max_concurrent_trials,
            oss_backup=oss_backup,
        )

        # Store state on app.state instead of global variable
        app.state.server_state = DashboardServerState(
            dashboard=dashboard,
            trial_manager=trial_manager,
            trace_backend=trace_backend,
            oss_backup=oss_backup,
        )

        # Start trial manager worker
        await trial_manager.start()

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
                )
                set_sls_log_exporter(sls_log_exporter)
                LOGGER.info(
                    "SLS Log exporter configured: %s/%s (flat fields)",
                    sls_project,
                    sls_logstore,
                )

        elif trace_backend == "jaeger":
            # Jaeger backend: use provided endpoint or default
            otlp_endpoint = trace_ingest_endpoint or "http://localhost:4318"
            otel_exporter = OTelSpanExporter(otlp_endpoint, headers=None)
            set_otel_exporter(otel_exporter)
            LOGGER.info("OTel exporter configured: %s (backend: jaeger)", otlp_endpoint)

        LOGGER.info(
            "Dashboard Server started (max_concurrent_trials=%d)",
            max_concurrent_trials,
        )
        yield

        # Graceful shutdown: stop trial manager (handles all running trials)
        LOGGER.info("Dashboard Server shutting down - stopping trial manager")
        try:
            await trial_manager.stop()
        except Exception as e:
            LOGGER.error("Error stopping trial manager: %s", e)

        # Also stop any trials that might be running directly in dashboard
        try:
            running_trials = [
                status
                for status in dashboard.list_trials()
                if status.phase.value in ("running", "starting")
            ]
            for trial_status in running_trials:
                try:
                    LOGGER.info(
                        "Stopping trial '%s' due to server shutdown",
                        trial_status.trial_id,
                    )
                    await dashboard.stop_trial(trial_status.trial_id)
                except Exception as e:
                    LOGGER.warning(
                        "Failed to stop trial '%s': %s", trial_status.trial_id, e
                    )
                    # Still emit a terminated span even if stop fails
                    dashboard._emit_trial_lifecycle_span(
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

        from ._tracing import get_sls_log_exporter

        sls_log_exp = get_sls_log_exporter()
        if sls_log_exp is not None:
            sls_log_exp.shutdown()
            set_sls_log_exporter(None)

        LOGGER.info("Dashboard Server shutdown complete")

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
                "metadata": dict(queued.spec.metadata),
                "error": queued.error,
                "source": "queue",
            }
            result.append(trial_info)
            seen_ids.add(queued.trial_id)

        # Then add trials from Dashboard (active/historical)
        for trial_status in state.dashboard.list_trials():
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
            "replay": {"file": "/path/to/events.jsonl", "speed_up": 1.0, "max_sleep": 20.0}
        }

        Request body (option 2 - params):
        {
            "trial_id": "optional-trial-id",
            "params": {"scenario": {"name": "..."}, ...},
            "resume": {...},
            "replay": {...}
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

        # Build the trial spec
        try:
            spec = definition.build(trial_id, scenario.config or scenario_config)
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

        # Handle replay configuration
        launch_coro_factory = None
        if request.replay:
            replay_file = Path(request.replay.file)
            if not replay_file.exists():
                return JSONResponse(
                    content={"error": f"Replay file not found: {replay_file}"},
                    status_code=400,
                )
            # Capture replay settings (for closure below)
            replay_speed_up = request.replay.speed_up
            replay_max_sleep = request.replay.max_sleep

            # Add replay metadata
            spec.metadata["replay_file"] = str(replay_file)
            spec.metadata["replay_mode"] = True
            spec.metadata["replay_speed_up"] = replay_speed_up
            spec.metadata["replay_max_sleep"] = replay_max_sleep
            spec.metadata["builder_name"] = scenario.name

            # Create factory for replay launch
            def make_replay_coro() -> Coroutine[Any, Any, TrialStatus]:
                return _launch_replay_trial(
                    dashboard=state.dashboard,
                    spec=spec,
                    replay_file=replay_file,
                    speed_up=replay_speed_up,
                    max_sleep=replay_max_sleep,
                )

            launch_coro_factory = make_replay_coro

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
                    "metadata": dict(queued.spec.metadata),
                    "error": queued.error,
                    "source": "queue",
                }
            )

        # Fall back to Dashboard for active/completed trials
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
                "source": "dashboard",
            }
        )

    @app.post("/api/trials/{trial_id}/stop")
    async def stop_trial(
        trial_id: str,
        state: DashboardServerState = Depends(get_server_state),
    ) -> JSONResponse:
        """Stop a running trial."""
        try:
            status = await state.dashboard.stop_trial(trial_id)
        except TrialNotFoundError:
            return JSONResponse(
                content={"error": f"Trial '{trial_id}' not found"},
                status_code=404,
            )

        # OSS backup if enabled
        oss_uploaded = False
        if state.oss_backup:
            # Get persistence_file from trial metadata
            try:
                trial_status = state.dashboard.get_trial_status(trial_id)
                persistence_file_path = trial_status.metadata.get("persistence_file")
                if persistence_file_path and isinstance(persistence_file_path, str):
                    persistence_file = Path(persistence_file_path)
                    oss_uploaded = _upload_trial_to_oss(trial_id, persistence_file)
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
            status = await state.dashboard.stop_trial(trial_id)
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
    trace_backend: str | None = None,
    trace_ingest_endpoint: str | None = None,
    oss_backup: bool = False,
    max_concurrent_trials: int = 20,
) -> None:
    """Run the Dashboard Server.

    Args:
        dashboard: Dashboard instance
        host: Host to bind to
        port: Port to listen on
        trace_backend: Trace backend type ("jaeger" or "sls"), or None to disable tracing
        trace_ingest_endpoint: OTLP endpoint for Jaeger (only used when trace_backend="jaeger")
        oss_backup: Enable OSS backup for trial data when trials complete
        max_concurrent_trials: Maximum number of concurrent running trials (default 20)
    """
    import uvicorn

    app = create_dashboard_app(
        dashboard,
        trace_backend=trace_backend,
        trace_ingest_endpoint=trace_ingest_endpoint,
        oss_backup=oss_backup,
        max_concurrent_trials=max_concurrent_trials,
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
    "DashboardServerState",
    "QueuedTrial",
    "QueuedTrialPhase",
    "TrialManager",
    "create_dashboard_app",
    "get_server_state",
    "run_dashboard_server",
]
