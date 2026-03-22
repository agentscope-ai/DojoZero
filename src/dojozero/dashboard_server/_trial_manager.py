"""Trial Manager for DojoZero Dashboard Server.

Handles queuing and concurrent execution of trials.
Extracted from core/_dashboard_server.py for better separation of concerns.
"""

import asyncio
import logging
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine

from typing import TYPE_CHECKING

from dojozero.core import (
    TrialOrchestrator,
    TrialExistsError,
    TrialNotFoundError,
    TrialPhase,
    TrialRecord,
    TrialSpec,
    TrialStatus,
)
from dojozero.dashboard_server._jsonl_utils import (
    extract_game_result_from_jsonl,
    get_jsonl_last_modified,
)
from dojozero.gateway import NoOpAuthenticator

if TYPE_CHECKING:
    from ._gateway_routing import GatewayRouter
    from dojozero.gateway import AgentAuthenticator

LOGGER = logging.getLogger("dojozero.trial_manager")


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
    # Coroutine factory for launching (supports backtest mode)
    launch_coro_factory: Callable[[], Coroutine[Any, Any, TrialStatus]] | None = None


class TrialManager:
    """Async task manager for running trials with queuing.

    Features:
    - Accepts trial submissions and returns immediately
    - Queues trials and runs up to max_concurrent in parallel
    - Tracks trial status (pending, running, completed, failed)
    - Supports cancellation of pending/running trials

    Usage:
        manager = TrialManager(orchestrator, max_concurrent=20)
        await manager.start()  # Start background worker

        trial_id = await manager.submit(spec)  # Returns immediately
        status = manager.get_status(trial_id)  # Check status

        await manager.cancel(trial_id)  # Cancel if needed
        await manager.stop()  # Graceful shutdown
    """

    def __init__(
        self,
        orchestrator: TrialOrchestrator,
        max_concurrent: int = 20,
        oss_backup: bool = False,
        auto_resume: bool = True,
        stale_threshold_hours: float = 24.0,
        checkpoint_interval_seconds: float = 300.0,
        gateway_router: "GatewayRouter | None" = None,
        gateway_grace_period: float = 5.0,
        authenticator: "AgentAuthenticator | None" = None,
    ):
        """Initialize the TrialManager.

        Args:
            orchestrator: TrialOrchestrator instance for launching trials
            max_concurrent: Maximum number of concurrent running trials
            oss_backup: Enable OSS backup when trials complete
            auto_resume: Automatically resume interrupted trials on startup
            stale_threshold_hours: Skip resuming trials older than this (hours)
            checkpoint_interval_seconds: Interval for periodic checkpointing (default 5 min)
            gateway_router: GatewayRouter instance for registering trial gateways
            gateway_grace_period: Seconds to wait after trial_ended before unregistering
                                  gateway (default 5s). This gives external agents time
                                  to receive the trial_ended SSE event and make final
                                  API calls (e.g., get_results).
            authenticator: AgentAuthenticator for validating agent API keys.
                          If None, uses NoOpAuthenticator (no auth).
        """
        self._orchestrator = orchestrator
        self._max_concurrent = max_concurrent
        self._oss_backup = oss_backup
        self._auto_resume = auto_resume
        self._stale_threshold_hours = stale_threshold_hours
        self._checkpoint_interval_seconds = checkpoint_interval_seconds
        self._gateway_router = gateway_router
        self._gateway_grace_period = gateway_grace_period
        self._authenticator = authenticator

        # Queue for pending trials
        self._pending: asyncio.Queue[QueuedTrial] = asyncio.Queue()

        # Track all trials by ID
        self._trials: dict[str, QueuedTrial] = {}

        # Track running tasks
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

        # Background worker task
        self._worker_task: asyncio.Task[None] | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._checkpoint_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()

        self._logger = logging.getLogger("dojozero.trial_manager")

    @property
    def orchestrator(self) -> TrialOrchestrator:
        """Get the orchestrator instance."""
        return self._orchestrator

    def _register_gateway(self, trial_id: str, spec: TrialSpec) -> bool:
        """Create and register a gateway for a trial.

        Args:
            trial_id: Trial identifier
            spec: Trial specification

        Returns:
            True if gateway was registered, False otherwise
        """
        if self._gateway_router is None:
            return False

        try:
            from dojozero.gateway import create_gateway_app, GatewayState
            from dojozero.gateway._adapter import ExternalAgentAdapter
            from dojozero.betting import BrokerOperator

            # Get trial runtime from orchestrator
            runtime = self._orchestrator._trials.get(trial_id)
            if runtime is None:
                self._logger.warning(
                    "Cannot register gateway: trial '%s' not found in orchestrator",
                    trial_id,
                )
                return False

            context = runtime._context
            if context is None:
                self._logger.warning(
                    "Cannot register gateway: trial '%s' has no runtime context",
                    trial_id,
                )
                return False

            # Get DataHub from context
            if not context.data_hubs:
                self._logger.warning(
                    "Cannot register gateway: trial '%s' has no DataHubs",
                    trial_id,
                )
                return False

            # Get the first DataHub
            hub_id = next(iter(context.data_hubs.keys()))
            data_hub = context.data_hubs[hub_id]

            # Find BrokerOperator from running actors
            broker: BrokerOperator | None = None
            for actor_runtime in runtime.actors.values():
                actor = actor_runtime.instance
                if isinstance(actor, BrokerOperator):
                    broker = actor
                    break

            if broker is None:
                self._logger.warning(
                    "Cannot register gateway: trial '%s' has no BrokerOperator",
                    trial_id,
                )
                return False

            # Get metadata for the gateway
            metadata: dict[str, Any] = {}
            if isinstance(spec.metadata, dict):
                metadata = spec.metadata
            elif hasattr(spec.metadata, "model_dump"):  # Pydantic
                metadata = spec.metadata.model_dump()
            elif is_dataclass(spec.metadata) and not isinstance(
                spec.metadata, type
            ):  # Dataclass instance
                metadata = asdict(spec.metadata)

            # Create gateway app (note: lifespan won't run since we're not using uvicorn)
            app = create_gateway_app(
                trial_id=trial_id,
                data_hub=data_hub,
                broker=broker,
                metadata=metadata,
                authenticator=self._authenticator,
            )

            # Create adapter and state manually since lifespan doesn't run for in-process routing
            adapter = ExternalAgentAdapter(
                data_hub=data_hub,
                broker=broker,
                trial_id=trial_id,
            )

            gateway_state = GatewayState(
                trial_id=trial_id,
                data_hub=data_hub,
                broker=broker,
                adapter=adapter,
                authenticator=self._authenticator or NoOpAuthenticator(),
                metadata=metadata,
            )

            # Ensure broker has event initialized from metadata if missing
            # This handles cases where broker was restored from checkpoint without GameInitializeEvent
            if broker._event is None and metadata:
                event_id = metadata.get("espn_game_id", trial_id)
                home_team = metadata.get("home_team_name", "")
                away_team = metadata.get("away_team_name", "")
                if home_team and away_team:
                    broker.ensure_event_initialized(
                        event_id=event_id,
                        home_team=home_team,
                        away_team=away_team,
                    )
                    self._logger.info(
                        "Initialized broker event from metadata for trial '%s': %s vs %s",
                        trial_id,
                        home_team,
                        away_team,
                    )
                else:
                    self._logger.warning(
                        "Cannot initialize broker event: missing team names for trial '%s'",
                        trial_id,
                    )

            # Set state on app for request handlers to access
            app.state.gateway_state = gateway_state

            # Register with router
            self._gateway_router.register_gateway(trial_id, app, gateway_state)
            self._logger.info("Registered gateway for trial '%s'", trial_id)
            return True

        except Exception as e:
            self._logger.error(
                "Failed to register gateway for trial '%s': %s",
                trial_id,
                e,
                exc_info=True,
            )
            return False

    def _unregister_gateway(self, trial_id: str) -> bool:
        """Unregister a gateway for a trial.

        Args:
            trial_id: Trial identifier

        Returns:
            True if gateway was unregistered, False otherwise
        """
        if self._gateway_router is None:
            return False

        if self._gateway_router.unregister_gateway(trial_id):
            self._logger.info("Unregistered gateway for trial '%s'", trial_id)
            return True
        return False

    async def _save_trial_results(
        self, trial_id: str, final_phase: QueuedTrialPhase
    ) -> bool:
        """Save trial results by querying the broker directly.

        This method works for ALL trials, regardless of whether gateway is used.
        It queries the broker operator for agent balances and statistics,
        then persists results.json to the trial directory.

        Args:
            trial_id: Trial identifier
            final_phase: Final phase of the trial

        Returns:
            True if results were saved successfully, False otherwise
        """
        # Import here to avoid circular imports
        from dojozero.betting import BrokerOperator
        from dojozero.gateway._models import AgentResult, TrialResultsResponse

        try:
            # Get the runtime to access actors
            runtime = self._orchestrator._trials.get(trial_id)
            if runtime is None:
                self._logger.warning(
                    "Cannot save results: trial '%s' runtime not found", trial_id
                )
                return False

            # Find BrokerOperator from running actors
            broker: BrokerOperator | None = None
            for actor_runtime in runtime.actors.values():
                actor = actor_runtime.instance
                if isinstance(actor, BrokerOperator):
                    broker = actor
                    break

            if broker is None:
                self._logger.warning(
                    "Cannot save results: trial '%s' has no BrokerOperator", trial_id
                )
                return False

            # Try to get gateway adapter for external agent info (display_name, authenticated)
            gateway_adapter = None
            if self._gateway_router is not None:
                gateway_state = self._gateway_router.get_gateway_state(trial_id)
                if gateway_state is not None:
                    gateway_adapter = gateway_state.adapter

            # Build results from broker (same logic as gateway adapter)
            agent_results: list[AgentResult] = []
            for agent_id in broker._accounts:
                try:
                    stats = await broker.get_statistics(agent_id)
                    account = broker._accounts.get(agent_id)
                    if account is None:
                        continue

                    # Get display_name from gateway if agent is currently connected
                    display_name = None
                    agent_state = None
                    if gateway_adapter is not None:
                        agent_state = gateway_adapter._agents.get(agent_id)
                        if agent_state is not None:
                            display_name = agent_state.display_name

                    # Use agent_state.authenticated if connected, otherwise fall back
                    # to account.is_external (persisted) - external agents are
                    # authenticated by definition since API key is required
                    if agent_state is not None:
                        authenticated = agent_state.authenticated
                    else:
                        authenticated = account.is_external

                    agent_results.append(
                        AgentResult(
                            agent_id=agent_id,
                            display_name=display_name,
                            authenticated=authenticated,
                            final_balance=str(account.balance),
                            net_profit=str(stats.net_profit),
                            total_bets=stats.total_bets,
                            win_rate=round(stats.win_rate, 4),
                            roi=round(stats.roi, 4),
                        )
                    )
                except Exception as e:
                    self._logger.warning(
                        "Failed to get results for agent %s: %s", agent_id, e
                    )

            # Sort by balance descending
            agent_results.sort(key=lambda r: float(r.final_balance), reverse=True)

            # Map phase to status string
            status_map = {
                QueuedTrialPhase.COMPLETED: "completed",
                QueuedTrialPhase.CANCELLED: "cancelled",
                QueuedTrialPhase.FAILED: "failed",
            }
            status = status_map.get(final_phase, "completed")

            # Build response
            results_response = TrialResultsResponse(
                trial_id=trial_id,
                status=status,
                results=agent_results,
                ended_at=datetime.now(timezone.utc),
            )

            # Save to store
            results_dict = results_response.model_dump(mode="json", by_alias=True)
            self._orchestrator.store.save_trial_results(trial_id, results_dict)
            self._logger.info(
                "Saved trial results for '%s' (%d agents)", trial_id, len(agent_results)
            )
            return True

        except Exception as e:
            self._logger.error(
                "Failed to save results for trial '%s': %s", trial_id, e, exc_info=True
            )
            return False

    async def _signal_trial_ended_and_cleanup(
        self, trial_id: str, final_phase: QueuedTrialPhase
    ) -> None:
        """Signal trial ended to SSE clients and then unregister gateway.

        This gives connected agents time to receive the trial_ended event
        before the gateway is removed.

        Args:
            trial_id: Trial identifier
            final_phase: Final phase of the trial
        """
        if self._gateway_router is None:
            return

        gateway_state = self._gateway_router.get_gateway_state(trial_id)
        if gateway_state is None:
            return

        # Map QueuedTrialPhase to reason string
        reason_map = {
            QueuedTrialPhase.COMPLETED: "completed",
            QueuedTrialPhase.CANCELLED: "cancelled",
            QueuedTrialPhase.FAILED: "failed",
        }
        reason = reason_map.get(final_phase, "completed")

        # Signal trial ended
        try:
            await gateway_state.adapter.signal_trial_ended(
                reason=reason,
                message=f"Trial {trial_id} has {reason}",
            )
            self._logger.info(
                "Signaled trial_ended for trial '%s' (reason=%s), "
                "waiting %.1fs grace period",
                trial_id,
                reason,
                self._gateway_grace_period,
            )

            # Note: Results are saved by _save_trial_results() which is called
            # for ALL trials (not just gateway ones) before this cleanup runs.

            # Grace period to allow SSE clients to receive the message
            # and make final API calls (e.g., get_results)
            await asyncio.sleep(self._gateway_grace_period)

        except Exception as e:
            self._logger.warning(
                "Failed to signal trial_ended for trial '%s': %s",
                trial_id,
                e,
            )

        # Now unregister the gateway
        self._unregister_gateway(trial_id)

    async def start(self) -> None:
        """Start the background worker and optionally resume interrupted trials."""
        if self._worker_task is not None:
            return
        self._shutdown_event.clear()
        self._worker_task = asyncio.create_task(self._worker_loop())
        self._status_task = asyncio.create_task(self._status_loop())
        self._checkpoint_task = asyncio.create_task(self._checkpoint_loop())
        self._logger.info(
            "TrialManager started (max_concurrent=%d, auto_resume=%s, checkpoint_interval=%.0fs)",
            self._max_concurrent,
            self._auto_resume,
            self._checkpoint_interval_seconds,
        )

        # Resume interrupted trials if enabled
        if self._auto_resume:
            resumed_count = await self._resume_interrupted_trials()
            if resumed_count > 0:
                self._logger.info(
                    "Resumed %d interrupted trial(s) from previous session",
                    resumed_count,
                )

    async def _resume_interrupted_trials(self) -> int:
        """Resume trials that were running/starting when the server shut down.

        Looks for trials in the orchestrator store with status RUNNING or STARTING,
        checks if they have checkpoints, and resubmits them with resume_from_latest.

        Returns:
            Number of trials successfully queued for resume
        """
        store = self._orchestrator.store
        now = datetime.now(timezone.utc)
        stale_threshold = timedelta(hours=self._stale_threshold_hours)

        resumed_count = 0
        skipped_stale = 0
        skipped_no_checkpoint = 0

        # Get all trial records from the store
        try:
            records = store.list_trial_records()
        except Exception as e:
            self._logger.warning("Failed to list trial records for resume: %s", e)
            return 0

        for record in records:
            trial_id = record.trial_id
            status = record.last_status

            # Skip trials that weren't running/starting
            if status is None:
                continue
            if status.phase not in (TrialPhase.RUNNING, TrialPhase.STARTING):
                continue

            # Check if trial is stale (based on checkpoint timestamp or skip)
            checkpoints = store.list_checkpoints(trial_id)
            if checkpoints:
                latest_checkpoint = max(checkpoints, key=lambda c: c.created_at)
                checkpoint_age = now - latest_checkpoint.created_at
                if checkpoint_age > stale_threshold:
                    self._logger.info(
                        "Skipping stale trial '%s' (checkpoint age: %s)",
                        trial_id,
                        checkpoint_age,
                    )
                    skipped_stale += 1
                    continue
            else:
                # No checkpoint available - cannot resume safely
                self._logger.warning(
                    "Cannot resume trial '%s' - no checkpoint available. "
                    "Trial will be marked as failed.",
                    trial_id,
                )
                skipped_no_checkpoint += 1
                # Update the status to FAILED so we don't keep trying to resume it
                if record.last_status is not None:
                    failed_status = TrialStatus(
                        trial_id=trial_id,
                        phase=TrialPhase.FAILED,
                        actors=record.last_status.actors,
                        metadata=record.last_status.metadata,
                        last_error="Server shutdown without checkpoint - cannot resume",
                    )
                    failed_record = TrialRecord(
                        spec=record.spec, last_status=failed_status
                    )
                    store.upsert_trial_record(failed_record)
                continue

            # Resume via orchestrator and track in TrialManager
            try:
                self._logger.info(
                    "Auto-resuming trial '%s' from checkpoint '%s'",
                    trial_id,
                    latest_checkpoint.checkpoint_id,
                )
                # Resume directly via orchestrator
                await self._orchestrator.resume_trial(
                    trial_id, checkpoint_id=latest_checkpoint.checkpoint_id
                )

                # Track the resumed trial in TrialManager so complete_trial() works
                # This ensures status consistency between TrialManager and Orchestrator
                queued = QueuedTrial(
                    trial_id=trial_id,
                    spec=record.spec,
                    phase=QueuedTrialPhase.RUNNING,
                )
                self._trials[trial_id] = queued

                # Register gateway for resumed trial
                if self._gateway_router is not None:
                    self._register_gateway(trial_id, record.spec)

                # Create monitoring task for resumed trial
                # This ensures results.json is saved when trial completes
                task = asyncio.create_task(self._monitor_resumed_trial(queued))
                self._running_tasks[trial_id] = task

                resumed_count += 1
                self._logger.info(
                    "Successfully resumed interrupted trial '%s'",
                    trial_id,
                )
            except TrialExistsError:
                # Trial already running (shouldn't happen, but handle gracefully)
                self._logger.debug(
                    "Trial '%s' already exists, skipping resume", trial_id
                )
            except Exception as e:
                self._logger.error("Failed to resume trial '%s': %s", trial_id, e)

        if skipped_stale > 0 or skipped_no_checkpoint > 0:
            self._logger.info(
                "Resume summary: queued=%d, skipped_stale=%d, skipped_no_checkpoint=%d",
                resumed_count,
                skipped_stale,
                skipped_no_checkpoint,
            )

        return resumed_count

    async def _status_loop(self) -> None:
        """Periodic status logging loop."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(30.0)  # Log every 30 seconds
                self._log_status()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error("Status loop error: %s", e)

    async def _checkpoint_loop(self) -> None:
        """Periodic checkpoint loop for all running trials."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self._checkpoint_interval_seconds)
                await self._checkpoint_all_running_trials()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error("Checkpoint loop error: %s", e)

    async def _checkpoint_all_running_trials(self) -> None:
        """Create checkpoints for all currently running trials."""
        # Get list of running trial IDs from orchestrator
        running_trial_ids = list(self._running_tasks.keys())
        if not running_trial_ids:
            return

        checkpoint_count = 0
        for trial_id in running_trial_ids:
            try:
                # Check if trial is still running in orchestrator
                status = self._orchestrator.get_trial_status(trial_id)
                if status.phase != TrialPhase.RUNNING:
                    continue

                # Create checkpoint
                checkpoint_id = await self._orchestrator.checkpoint_trial(trial_id)
                checkpoint_count += 1
                self._logger.debug(
                    "Checkpoint created for trial '%s': %s", trial_id, checkpoint_id
                )
            except TrialNotFoundError:
                # Trial no longer exists, skip
                continue
            except Exception as e:
                self._logger.warning("Failed to checkpoint trial '%s': %s", trial_id, e)

        if checkpoint_count > 0:
            self._logger.info(
                "Periodic checkpoint: saved %d/%d running trials",
                checkpoint_count,
                len(running_trial_ids),
            )

    def _log_status(self) -> None:
        """Log current trial manager status."""
        # Clean up completed tasks before reporting status
        # This ensures cancelled/completed tasks are removed even when below max capacity
        self._cleanup_completed_tasks()

        running_ids = list(self._running_tasks.keys())
        pending_count = self._pending.qsize()

        if running_ids or pending_count > 0:
            self._logger.info(
                "TrialManager status: running=%d/%d, pending=%d, running_ids=%s",
                len(running_ids),
                self._max_concurrent,
                pending_count,
                running_ids,
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

        # Cancel status task
        if self._status_task is not None:
            self._status_task.cancel()
            try:
                await self._status_task
            except asyncio.CancelledError:
                pass
            self._status_task = None

        # Cancel checkpoint task
        if self._checkpoint_task is not None:
            self._checkpoint_task.cancel()
            try:
                await self._checkpoint_task
            except asyncio.CancelledError:
                pass
            self._checkpoint_task = None

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
        launch_coro_factory: Callable[[], Coroutine[Any, Any, TrialStatus]]
        | None = None,
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
            RuntimeError: If the trial manager has been stopped
        """
        # Check if manager has been stopped
        if self._shutdown_event.is_set():
            raise RuntimeError("TrialManager is not running")

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

    def _get_jsonl_path(self, trial_id: str) -> Path | None:
        """Get JSONL persistence file path from trial spec metadata.

        Args:
            trial_id: Trial identifier

        Returns:
            Path to JSONL file, or None if not found/configured
        """
        queued = self.get_status(trial_id)
        if not queued or not queued.spec:
            return None
        metadata = queued.spec.metadata
        # Handle both dict and Pydantic model metadata
        if isinstance(metadata, dict):
            persistence_file = metadata.get("persistence_file")
        else:
            persistence_file = getattr(metadata, "persistence_file", None)
        if persistence_file:
            return Path(persistence_file)
        return None

    async def cancel(self, trial_id: str) -> bool:
        """Cancel a pending or running trial (marks as CANCELLED).

        Use this when a user explicitly cancels a trial before it completes.
        For trials that finish normally (e.g., game ended), use complete_trial() instead.

        Args:
            trial_id: Trial identifier

        Returns:
            True if cancelled, False if not found or already completed
        """
        return await self._stop_trial_internal(trial_id, QueuedTrialPhase.CANCELLED)

    async def complete_trial(self, trial_id: str) -> bool:
        """Stop a running trial gracefully (marks as COMPLETED).

        Use this when a trial completes normally (e.g., game ended).
        For user-initiated cancellation, use cancel() instead.

        Args:
            trial_id: Trial identifier

        Returns:
            True if stopped, False if not found or already completed
        """
        return await self._stop_trial_internal(trial_id, QueuedTrialPhase.COMPLETED)

    async def _stop_trial_internal(
        self, trial_id: str, final_phase: QueuedTrialPhase
    ) -> bool:
        """Internal method to stop a trial with specified final phase.

        Args:
            trial_id: Trial identifier
            final_phase: Phase to set (CANCELLED or COMPLETED)

        Returns:
            True if stopped, False if not found or already completed
        """
        phase_name = final_phase.value
        queued = self._trials.get(trial_id)

        if queued is None:
            # Trial not in _trials - this should not happen.
            # Trials should be tracked in _trials via submit() or auto-resume.
            self._logger.error(
                "Trial '%s' not found in _trials. "
                "This indicates a bug in trial tracking.",
                trial_id,
            )
            return False

        if queued.phase == QueuedTrialPhase.PENDING:
            # Mark with final phase (will be skipped by worker)
            queued.phase = final_phase
            self._logger.info("%s pending trial: %s", phase_name.capitalize(), trial_id)
            return True

        if queued.phase in (QueuedTrialPhase.STARTING, QueuedTrialPhase.RUNNING):
            # Update phase first
            queued.phase = final_phase
            self._logger.info("%s trial: %s", phase_name.capitalize(), trial_id)

            # Cancel task if still running
            task = self._running_tasks.get(trial_id)
            if task and not task.done():
                task.cancel()

            # Always stop via orchestrator to update its internal state
            # (even if our task is done, the orchestrator may not know)
            try:
                await self._orchestrator.stop_trial(trial_id)
            except Exception as e:
                self._logger.error("Error stopping trial %s: %s", trial_id, e)

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
                    queued = await asyncio.wait_for(self._pending.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                # Skip cancelled trials
                if queued.phase == QueuedTrialPhase.CANCELLED:
                    self._logger.debug("Skipping cancelled trial: %s", queued.trial_id)
                    continue

                # Launch trial in background task
                task = asyncio.create_task(self._run_trial(queued))
                self._running_tasks[queued.trial_id] = task
                self._logger.info(
                    "Launched trial '%s' (running=%d/%d, pending=%d)",
                    queued.trial_id,
                    len(self._running_tasks),
                    self._max_concurrent,
                    self._pending.qsize(),
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error("Worker error: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

    def _cleanup_completed_tasks(self) -> None:
        """Remove completed tasks from running dict."""
        completed = [
            trial_id for trial_id, task in self._running_tasks.items() if task.done()
        ]
        for trial_id in completed:
            del self._running_tasks[trial_id]
            self._logger.info(
                "Trial '%s' task completed (running=%d/%d)",
                trial_id,
                len(self._running_tasks),
                self._max_concurrent,
            )

    async def _monitor_resumed_trial(self, queued: QueuedTrial) -> None:
        """Monitor a resumed trial until completion.

        This is similar to the monitoring loop in _run_trial, but for trials
        that were resumed from checkpoint (already running in orchestrator).
        """
        trial_id = queued.trial_id
        self._logger.info("Monitoring resumed trial: %s", trial_id)

        try:
            # Wait for trial to complete by monitoring orchestrator status
            last_health_warning: datetime | None = None
            game_result_detected_at: datetime | None = None

            while True:
                await asyncio.sleep(2.0)
                try:
                    status = self._orchestrator.get_trial_status(trial_id)
                    if status.phase.value in ("completed", "stopped", "failed"):
                        break

                    # Get JSONL path for backup checks
                    jsonl_path = self._get_jsonl_path(trial_id)

                    # Backup: check JSONL for game_result
                    if jsonl_path and extract_game_result_from_jsonl(jsonl_path):
                        now = datetime.now(timezone.utc)
                        if game_result_detected_at is None:
                            game_result_detected_at = now
                            self._logger.info(
                                "Game result detected in JSONL for resumed trial '%s', "
                                "waiting for normal self-stop mechanism (10s grace period)",
                                trial_id,
                            )
                        elif (now - game_result_detected_at) > timedelta(seconds=10):
                            self._logger.info(
                                "Resumed trial '%s' still running 10s after game_result, "
                                "stopping via JSONL backup mechanism",
                                trial_id,
                            )
                            await self._orchestrator.stop_trial(trial_id)
                            break

                    # Health check: warn if no events for 10 minutes
                    if jsonl_path:
                        last_modified = get_jsonl_last_modified(jsonl_path)
                        if last_modified:
                            stale_duration = datetime.now(timezone.utc) - last_modified
                            if stale_duration > timedelta(minutes=10):
                                now = datetime.now(timezone.utc)
                                if last_health_warning is None or (
                                    now - last_health_warning
                                ) > timedelta(minutes=10):
                                    self._logger.warning(
                                        "Resumed trial '%s' may be stuck "
                                        "(no new events for %.1f min)",
                                        trial_id,
                                        stale_duration.total_seconds() / 60,
                                    )
                                    last_health_warning = now
                except TrialNotFoundError:
                    break
                except Exception as e:
                    self._logger.warning(
                        "Error in monitoring loop for resumed trial '%s': %s",
                        trial_id,
                        e,
                    )

            # Check final status
            try:
                status = self._orchestrator.get_trial_status(trial_id)
                if status.phase.value == "failed":
                    queued.phase = QueuedTrialPhase.FAILED
                    queued.error = status.last_error
                else:
                    queued.phase = QueuedTrialPhase.COMPLETED
            except TrialNotFoundError:
                queued.phase = QueuedTrialPhase.COMPLETED

            self._logger.info(
                "Resumed trial '%s' finished with phase: %s",
                trial_id,
                queued.phase.value,
            )

        except asyncio.CancelledError:
            # Check if trial is still running in orchestrator
            # If so, this is likely a server shutdown - don't finalize results
            # Let the restarted server's monitoring task handle it
            try:
                status = self._orchestrator.get_trial_status(trial_id)
                if status.phase == TrialPhase.RUNNING:
                    self._logger.info(
                        "Monitoring task cancelled but trial '%s' still running - "
                        "skipping results finalization (will be handled on resume)",
                        trial_id,
                    )
                    raise  # Don't save results, just propagate cancellation
            except TrialNotFoundError:
                pass  # Trial gone, continue with cancellation handling
            except Exception:
                pass  # Error checking status, continue with cancellation handling

            queued.phase = QueuedTrialPhase.CANCELLED
            self._logger.info("Resumed trial '%s' was cancelled", trial_id)
            raise
        except Exception as e:
            queued.phase = QueuedTrialPhase.FAILED
            queued.error = str(e)
            self._logger.error(
                "Resumed trial '%s' failed: %s", trial_id, e, exc_info=True
            )
        finally:
            # Only save results if we have a final phase set
            # (cancelled trials that are still running won't have phase updated)
            if queued.phase != QueuedTrialPhase.RUNNING:
                await self._save_trial_results(trial_id, queued.phase)

            # Clean up gateway if registered
            if self._gateway_router is not None:
                gateway_state = self._gateway_router.get_gateway_state(trial_id)
                if gateway_state is not None:
                    await self._signal_trial_ended_and_cleanup(trial_id, queued.phase)

    async def _run_trial(self, queued: QueuedTrial) -> None:
        """Run a single trial."""
        trial_id = queued.trial_id
        self._logger.info("Starting trial: %s", trial_id)
        queued.phase = QueuedTrialPhase.STARTING
        gateway_registered = False

        try:
            # Launch via custom factory or default
            if queued.launch_coro_factory:
                await queued.launch_coro_factory()
            else:
                await self._orchestrator.launch_trial(queued.spec)

            queued.phase = QueuedTrialPhase.RUNNING
            self._logger.info("Trial '%s' is now running", trial_id)

            # Create initial checkpoint immediately so trial can always be resumed
            try:
                checkpoint_id = await self._orchestrator.checkpoint_trial(trial_id)
                self._logger.info(
                    "Initial checkpoint created for trial '%s': %s",
                    trial_id,
                    checkpoint_id,
                )
            except Exception as e:
                self._logger.warning(
                    "Failed to create initial checkpoint for trial '%s': %s",
                    trial_id,
                    e,
                )

            # Register gateway if router is available
            if self._gateway_router is not None:
                gateway_registered = self._register_gateway(trial_id, queued.spec)

            # Wait for trial to complete by monitoring dashboard status
            last_health_warning: datetime | None = None  # Track when we last warned
            game_result_detected_at: datetime | None = (
                None  # Track when game_result first seen
            )
            while True:
                await asyncio.sleep(2.0)
                try:
                    status = self._orchestrator.get_trial_status(trial_id)
                    if status.phase.value in ("completed", "stopped", "failed"):
                        break

                    # Get JSONL path for backup checks
                    jsonl_path = self._get_jsonl_path(trial_id)

                    # Backup: check JSONL for game_result (in case callback chain failed)
                    # Use a delay similar to the normal self-stop mechanism (10s for broker settlement)
                    if jsonl_path and extract_game_result_from_jsonl(jsonl_path):
                        now = datetime.now(timezone.utc)
                        if game_result_detected_at is None:
                            # First time seeing game_result - record timestamp, don't stop yet
                            game_result_detected_at = now
                            self._logger.info(
                                "Game result detected in JSONL for trial '%s', "
                                "waiting for normal self-stop mechanism (10s grace period)",
                                trial_id,
                            )
                        elif (now - game_result_detected_at) > timedelta(seconds=10):
                            # Game result was detected 10+ seconds ago but trial still running
                            # This means callback chain failed - stop as backup
                            self._logger.info(
                                "Trial '%s' still running 10s after game_result detected, "
                                "stopping via JSONL backup mechanism",
                                trial_id,
                            )
                            await self._orchestrator.stop_trial(trial_id)
                            break

                    # Health check: warn if no events for 10 minutes
                    if jsonl_path:
                        last_modified = get_jsonl_last_modified(jsonl_path)
                        if last_modified:
                            stale_duration = datetime.now(timezone.utc) - last_modified
                            if stale_duration > timedelta(minutes=10):
                                # Only warn once per 10-minute window
                                now = datetime.now(timezone.utc)
                                if last_health_warning is None or (
                                    now - last_health_warning
                                ) > timedelta(minutes=10):
                                    self._logger.warning(
                                        "Trial '%s' may be stuck (no new events for %.1f min)",
                                        trial_id,
                                        stale_duration.total_seconds() / 60,
                                    )
                                    last_health_warning = now
                except TrialNotFoundError:
                    # Trial removed from dashboard
                    break
                except Exception as e:
                    # Log but continue monitoring - don't crash the loop
                    # This makes the monitoring loop resilient to transient errors
                    self._logger.warning(
                        "Error in monitoring loop for trial '%s': %s",
                        trial_id,
                        e,
                    )

            # Check final status
            try:
                status = self._orchestrator.get_trial_status(trial_id)
                if status.phase.value == "failed":
                    queued.phase = QueuedTrialPhase.FAILED
                    queued.error = status.last_error
                else:
                    queued.phase = QueuedTrialPhase.COMPLETED
            except TrialNotFoundError:
                queued.phase = QueuedTrialPhase.COMPLETED

            if self._oss_backup and queued.phase == QueuedTrialPhase.COMPLETED:
                self._upload_to_oss(trial_id, queued.spec)

            self._logger.info(
                "Trial '%s' finished with phase: %s", trial_id, queued.phase.value
            )

        except asyncio.CancelledError:
            # Check if trial is still running in orchestrator
            # If so, this is likely a server shutdown - don't finalize results
            # Let the restarted server's monitoring task handle it
            try:
                status = self._orchestrator.get_trial_status(trial_id)
                if status.phase == TrialPhase.RUNNING:
                    self._logger.info(
                        "Monitoring task cancelled but trial '%s' still running - "
                        "skipping results finalization (will be handled on resume)",
                        trial_id,
                    )
                    raise  # Don't save results, just propagate cancellation
            except TrialNotFoundError:
                pass  # Trial gone, continue with cancellation handling
            except Exception:
                pass  # Error checking status, continue with cancellation handling

            queued.phase = QueuedTrialPhase.CANCELLED
            self._logger.info("Trial '%s' was cancelled", trial_id)
            raise
        except Exception as e:
            queued.phase = QueuedTrialPhase.FAILED
            queued.error = str(e)
            self._logger.error("Trial '%s' failed: %s", trial_id, e, exc_info=True)
        finally:
            # Only save results if we have a final phase set
            # (cancelled trials that are still running won't have phase updated)
            if queued.phase != QueuedTrialPhase.RUNNING:
                # Save trial results (for ALL trials, not just gateway)
                # Must happen BEFORE gateway cleanup since we need access to broker
                await self._save_trial_results(trial_id, queued.phase)

            # Signal trial ended and unregister gateway (gateway-specific cleanup)
            if gateway_registered:
                await self._signal_trial_ended_and_cleanup(trial_id, queued.phase)

    def _upload_to_oss(self, trial_id: str, spec: TrialSpec) -> None:
        """Upload trial data to OSS if configured."""
        persistence_file_path = spec.metadata.get("persistence_file")
        if persistence_file_path and isinstance(persistence_file_path, str):
            persistence_file = Path(persistence_file_path)
            upload_trial_to_oss(trial_id, persistence_file)


# Lazy import for OSS to avoid import errors if oss2 not installed
_oss_client = None


def upload_trial_to_oss(trial_id: str, persistence_file: Path | None) -> bool:
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
        LOGGER.warning(
            "OSS backup skipped: optional OSS dependencies missing "
            "(pip install 'dojozero[alicloud]')"
        )
        return False
    except ValueError as e:
        LOGGER.warning("OSS backup failed - configuration error: %s", e)
        return False
    except Exception as e:
        LOGGER.error("OSS backup failed for trial %s: %s", trial_id, e)
        return False


__all__ = [
    "QueuedTrial",
    "QueuedTrialPhase",
    "TrialManager",
    "upload_trial_to_oss",
]
