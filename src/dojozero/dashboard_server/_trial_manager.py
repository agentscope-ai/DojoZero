"""Trial Manager for DojoZero Dashboard Server.

Handles queuing and concurrent execution of trials.
Extracted from core/_dashboard_server.py for better separation of concerns.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine

from dojozero.core import (
    TrialOrchestrator,
    TrialExistsError,
    TrialNotFoundError,
    TrialPhase,
    TrialRecord,
    TrialSpec,
    TrialStatus,
)

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
    ):
        """Initialize the TrialManager.

        Args:
            orchestrator: TrialOrchestrator instance for launching trials
            max_concurrent: Maximum number of concurrent running trials
            oss_backup: Enable OSS backup when trials complete
            auto_resume: Automatically resume interrupted trials on startup
            stale_threshold_hours: Skip resuming trials older than this (hours)
        """
        self._orchestrator = orchestrator
        self._max_concurrent = max_concurrent
        self._oss_backup = oss_backup
        self._auto_resume = auto_resume
        self._stale_threshold_hours = stale_threshold_hours

        # Queue for pending trials
        self._pending: asyncio.Queue[QueuedTrial] = asyncio.Queue()

        # Track all trials by ID
        self._trials: dict[str, QueuedTrial] = {}

        # Track running tasks
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

        # Background worker task
        self._worker_task: asyncio.Task[None] | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()

        self._logger = logging.getLogger("dojozero.trial_manager")

    @property
    def orchestrator(self) -> TrialOrchestrator:
        """Get the orchestrator instance."""
        return self._orchestrator

    async def start(self) -> None:
        """Start the background worker and optionally resume interrupted trials."""
        if self._worker_task is not None:
            return
        self._shutdown_event.clear()
        self._worker_task = asyncio.create_task(self._worker_loop())
        self._status_task = asyncio.create_task(self._status_loop())
        self._logger.info(
            "TrialManager started (max_concurrent=%d, auto_resume=%s)",
            self._max_concurrent,
            self._auto_resume,
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

            # Use orchestrator's resume_trial method directly (bypasses queue)
            # This is intentional - auto-resumed trials use the existing spec
            # and checkpoint, avoiding spec mismatch issues
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

    def _log_status(self) -> None:
        """Log current trial manager status."""
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
                    await self._orchestrator.stop_trial(trial_id)
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
                await self._orchestrator.launch_trial(queued.spec)

            queued.phase = QueuedTrialPhase.RUNNING
            self._logger.info("Trial '%s' is now running", trial_id)

            # Wait for trial to complete by monitoring dashboard status
            while True:
                await asyncio.sleep(2.0)
                try:
                    status = self._orchestrator.get_trial_status(trial_id)
                    if status.phase.value in ("completed", "stopped", "failed"):
                        break
                except TrialNotFoundError:
                    # Trial removed from dashboard
                    break

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
        LOGGER.warning("OSS backup requested but oss2 package not installed")
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
