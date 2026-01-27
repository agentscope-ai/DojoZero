import asyncio
from typing import Any, Mapping, TypedDict

import pytest

from dojozero.core import (
    RuntimeContext,
    Agent,
    AgentBase,
    AgentSpec,
    BaseTrialMetadata,
    TrialOrchestrator,
    DataStream,
    DataStreamBase,
    DataStreamSpec,
    FileSystemOrchestratorStore,
    InMemoryOrchestratorStore,
    Operator,
    OperatorBase,
    OperatorSpec,
    StreamEvent,
    TrialPhase,
    TrialSpec,
)
from dojozero.dashboard_server._trial_manager import (
    TrialManager,
    QueuedTrial,
    QueuedTrialPhase,
)
from dojozero.dashboard_server._scheduler import (
    ScheduleManager,
    ScheduledTrial,
    ScheduledTrialPhase,
)

CALL_LOG: list[tuple[str, str]] = []


def _record(actor_id: str, action: str) -> None:
    CALL_LOG.append((actor_id, action))


@pytest.fixture(autouse=True)
def _reset_call_log() -> None:
    CALL_LOG.clear()


class DummyOperatorConfig(TypedDict):
    actor_id: str


class DummyAgentConfig(TypedDict):
    actor_id: str


class DummyStreamConfig(TypedDict):
    actor_id: str


class DummyOperator(OperatorBase, Operator[DummyOperatorConfig]):
    def __init__(self, actor_id: str, trial_id: str) -> None:
        super().__init__(actor_id, trial_id)
        self.events_handled = 0
        self.restored_events = 0

    @classmethod
    def from_dict(
        cls,
        config: DummyOperatorConfig,
        context: RuntimeContext,
    ) -> "DummyOperator":
        return cls(actor_id=str(config["actor_id"]), trial_id=context.trial_id)

    async def start(self) -> None:
        _record(self.actor_id, "start")

    async def stop(self) -> None:
        _record(self.actor_id, "stop")

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        self.events_handled += 1

    async def save_state(self) -> Mapping[str, Any]:
        return {"events": self.events_handled}

    async def load_state(self, state: Mapping[str, Any]) -> None:
        self.restored_events = int(state.get("events", 0))
        self.events_handled = self.restored_events


class DummyAgent(AgentBase, Agent[DummyAgentConfig]):
    def __init__(self, actor_id: str, trial_id: str) -> None:
        super().__init__(actor_id, trial_id)
        self.event_count = 0
        self.restored_count = 0

    @classmethod
    def from_dict(
        cls,
        config: DummyAgentConfig,
        context: RuntimeContext,
    ) -> "DummyAgent":
        return cls(actor_id=str(config["actor_id"]), trial_id=context.trial_id)

    async def start(self) -> None:
        _record(self.actor_id, "start")

    async def stop(self) -> None:
        _record(self.actor_id, "stop")

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        self.event_count += 1

    async def save_state(self) -> Mapping[str, Any]:
        return {"events": self.event_count}

    async def load_state(self, state: Mapping[str, Any]) -> None:
        self.restored_count = int(state.get("events", 0))
        self.event_count = self.restored_count


class DummyDataStream(DataStreamBase, DataStream[DummyStreamConfig]):
    def __init__(self, actor_id: str, trial_id: str) -> None:
        super().__init__(actor_id, trial_id)
        self.emitted = 0
        self.restored_emissions = 0

    @classmethod
    def from_dict(
        cls,
        config: DummyStreamConfig,
        context: RuntimeContext,
    ) -> "DummyDataStream":
        return cls(actor_id=str(config["actor_id"]), trial_id=context.trial_id)

    async def start(self) -> None:
        _record(self.actor_id, "start")

    async def stop(self) -> None:
        _record(self.actor_id, "stop")

    async def emit(self, payload: Any) -> None:
        await self._publish(StreamEvent(stream_id=self.actor_id, payload=payload))
        self.emitted += 1

    async def save_state(self) -> Mapping[str, Any]:
        return {"emitted": self.emitted}

    async def load_state(self, state: Mapping[str, Any]) -> None:
        self.restored_emissions = int(state.get("emitted", 0))
        self.emitted = self.restored_emissions


@pytest.mark.asyncio
async def test_orchestrator_lifecycle_order() -> None:
    store = InMemoryOrchestratorStore()
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-lifecycle")

    await orchestrator.launch_trial(spec)

    status = orchestrator.get_trial_status("trial-lifecycle")
    assert status.phase is TrialPhase.RUNNING

    stream = orchestrator.get_actor("trial-lifecycle", "stream-1")
    assert isinstance(stream, DummyDataStream)
    assert set(stream.consumers) == {"op-1", "agent-1"}

    await orchestrator.stop_trial("trial-lifecycle")

    status = orchestrator.get_trial_status("trial-lifecycle")
    assert status.phase is TrialPhase.STOPPED
    # Ensure persisted status is visible after a new orchestrator instance is created.
    replacement_orchestrator = TrialOrchestrator(store=store)
    status = replacement_orchestrator.get_trial_status("trial-lifecycle")
    assert status.phase is TrialPhase.STOPPED
    assert replacement_orchestrator.list_trials()[0].trial_id == "trial-lifecycle"

    assert CALL_LOG == [
        ("op-1", "start"),
        ("agent-1", "start"),
        ("stream-1", "start"),
        ("stream-1", "stop"),
        ("agent-1", "stop"),
        ("op-1", "stop"),
    ]


@pytest.mark.asyncio
async def test_orchestrator_checkpoint_and_resume() -> None:
    store = InMemoryOrchestratorStore()
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-checkpoint")

    await orchestrator.launch_trial(spec)

    stream = orchestrator.get_actor("trial-checkpoint", "stream-1")
    assert isinstance(stream, DummyDataStream)
    await stream.emit({"price": 42})

    checkpoint = await orchestrator.checkpoint_trial("trial-checkpoint")
    agent_state = checkpoint.actor_states["agent-1"]
    assert agent_state["events"] == 1
    assert checkpoint.checkpoint_id is not None
    summaries = orchestrator.list_checkpoints("trial-checkpoint")
    assert summaries[-1].checkpoint_id == checkpoint.checkpoint_id

    await orchestrator.stop_trial("trial-checkpoint")

    resumed_orchestrator = TrialOrchestrator(store=store)
    status = await resumed_orchestrator.resume_trial(
        "trial-checkpoint", checkpoint_id=checkpoint.checkpoint_id
    )
    assert status.phase is TrialPhase.RUNNING

    resumed_agent = resumed_orchestrator.get_actor("trial-checkpoint", "agent-1")
    assert isinstance(resumed_agent, DummyAgent)
    assert resumed_agent.event_count == 1

    resumed_operator = resumed_orchestrator.get_actor("trial-checkpoint", "op-1")
    assert isinstance(resumed_operator, DummyOperator)
    assert resumed_operator.events_handled == 1

    resumed_stream = resumed_orchestrator.get_actor("trial-checkpoint", "stream-1")
    assert isinstance(resumed_stream, DummyDataStream)
    assert resumed_stream.restored_emissions == 1

    await resumed_orchestrator.stop_trial("trial-checkpoint")
    await resumed_orchestrator.delete_trial("trial-checkpoint")


@pytest.mark.asyncio
async def test_filesystem_orchestrator_store_persistence(tmp_path) -> None:
    store = FileSystemOrchestratorStore(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-fs")

    await orchestrator.launch_trial(spec)
    stream = orchestrator.get_actor("trial-fs", "stream-1")
    assert isinstance(stream, DummyDataStream)
    await stream.emit({"price": 7})
    checkpoint = await orchestrator.checkpoint_trial("trial-fs")
    await orchestrator.stop_trial("trial-fs")

    restored_orchestrator = TrialOrchestrator(store=store)
    checkpoints = restored_orchestrator.list_checkpoints("trial-fs")
    assert checkpoints
    assert checkpoints[-1].checkpoint_id == checkpoint.checkpoint_id
    status = restored_orchestrator.get_trial_status("trial-fs")
    assert status.phase is TrialPhase.STOPPED

    resumed_status = await restored_orchestrator.resume_trial("trial-fs")
    assert resumed_status.phase is TrialPhase.RUNNING
    resumed_agent = restored_orchestrator.get_actor("trial-fs", "agent-1")
    assert isinstance(resumed_agent, DummyAgent)
    assert resumed_agent.event_count == 1

    await restored_orchestrator.stop_trial("trial-fs")
    await restored_orchestrator.delete_trial("trial-fs")
    assert restored_orchestrator.list_trials() == ()


@pytest.mark.asyncio
async def test_launch_trial_uses_spec_resume_checkpoint() -> None:
    store = InMemoryOrchestratorStore()
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-resume-spec")

    await orchestrator.launch_trial(spec)
    stream = orchestrator.get_actor("trial-resume-spec", "stream-1")
    assert isinstance(stream, DummyDataStream)
    await stream.emit({"price": 101})
    checkpoint = await orchestrator.checkpoint_trial("trial-resume-spec")
    await orchestrator.stop_trial("trial-resume-spec")

    resumed_orchestrator = TrialOrchestrator(store=store)
    resumed_spec = _build_trial_spec("trial-resume-spec")
    resumed_spec.resume_from_checkpoint_id = checkpoint.checkpoint_id
    status = await resumed_orchestrator.launch_trial(resumed_spec)
    assert status.phase is TrialPhase.RUNNING

    resumed_agent = resumed_orchestrator.get_actor("trial-resume-spec", "agent-1")
    assert isinstance(resumed_agent, DummyAgent)
    assert resumed_agent.event_count == 1

    await resumed_orchestrator.stop_trial("trial-resume-spec")


def _build_trial_spec(
    trial_id: str,
    *,
    resume_states: Mapping[str, Mapping[str, Any]] | None = None,
) -> TrialSpec[BaseTrialMetadata]:
    resume_states = resume_states or {}
    operator_config: DummyOperatorConfig = {"actor_id": "op-1"}
    agent_config: DummyAgentConfig = {"actor_id": "agent-1"}
    stream_config: DummyStreamConfig = {"actor_id": "stream-1"}
    metadata = BaseTrialMetadata(
        hub_id="test_hub",
        persistence_file="/tmp/test.jsonl",
        store_types=(),
    )
    return TrialSpec(
        trial_id=trial_id,
        metadata=metadata,
        operators=(
            OperatorSpec(
                actor_id="op-1",
                actor_cls=DummyOperator,
                config=operator_config,
                resume_state=resume_states.get("op-1"),
                agent_ids=("agent-1",),
            ),
        ),
        agents=(
            AgentSpec(
                actor_id="agent-1",
                actor_cls=DummyAgent,
                config=agent_config,
                resume_state=resume_states.get("agent-1"),
                operator_ids=("op-1",),
            ),
        ),
        data_streams=(
            DataStreamSpec(
                actor_id="stream-1",
                actor_cls=DummyDataStream,
                config=stream_config,
                resume_state=resume_states.get("stream-1"),
                consumer_ids=("op-1", "agent-1"),
            ),
        ),
    )


# =============================================================================
# TrialManager Auto-Resume Tests
# =============================================================================


@pytest.mark.asyncio
async def test_trial_manager_auto_resume_with_checkpoint(tmp_path) -> None:
    """Test that TrialManager auto-resumes trials that were running at shutdown."""
    store = FileSystemOrchestratorStore(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-auto-resume")

    # Launch trial, emit event, checkpoint, then simulate crash (no graceful stop)
    await orchestrator.launch_trial(spec)
    stream = orchestrator.get_actor("trial-auto-resume", "stream-1")
    assert isinstance(stream, DummyDataStream)
    await stream.emit({"price": 100})
    await orchestrator.checkpoint_trial("trial-auto-resume")

    # Simulate server crash - status is still RUNNING in store
    status = orchestrator.get_trial_status("trial-auto-resume")
    assert status.phase is TrialPhase.RUNNING

    # Stop the trial in the old orchestrator (but keep status as RUNNING in store)
    # by directly manipulating the runtime without updating the store
    # Actually, let's just create a new orchestrator which won't have the running trial
    # in memory but the store still shows RUNNING status

    # Create a new orchestrator (simulating server restart)
    new_orchestrator = TrialOrchestrator(store=store)

    # Verify the store still shows RUNNING (from the crash)
    stored_status = new_orchestrator.get_trial_status("trial-auto-resume")
    assert stored_status.phase is TrialPhase.RUNNING

    # Create TrialManager with auto_resume enabled
    trial_manager = TrialManager(
        orchestrator=new_orchestrator,
        auto_resume=True,
        stale_threshold_hours=24.0,
    )

    # Start the manager - should auto-resume the interrupted trial
    await trial_manager.start()

    # Auto-resume happens synchronously during start(), so trial should be running
    resumed_status = new_orchestrator.get_trial_status("trial-auto-resume")
    assert resumed_status.phase is TrialPhase.RUNNING

    # Verify the auto-resumed trial is tracked in TrialManager._trials
    # This ensures complete_trial() will work correctly after server restart
    queued_status = trial_manager.get_status("trial-auto-resume")
    assert queued_status is not None, (
        "Auto-resumed trial should be tracked in TrialManager._trials"
    )
    assert queued_status.phase == QueuedTrialPhase.RUNNING

    # Verify the agent state was restored from checkpoint
    resumed_agent = new_orchestrator.get_actor("trial-auto-resume", "agent-1")
    assert isinstance(resumed_agent, DummyAgent)
    assert resumed_agent.event_count == 1  # Restored from checkpoint

    # Clean up
    await new_orchestrator.stop_trial("trial-auto-resume")
    await trial_manager.stop()


@pytest.mark.asyncio
async def test_trial_manager_auto_resume_disabled() -> None:
    """Test that auto-resume can be disabled."""
    store = InMemoryOrchestratorStore()
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-no-auto-resume")

    # Launch, checkpoint, leave running
    await orchestrator.launch_trial(spec)
    await orchestrator.checkpoint_trial("trial-no-auto-resume")

    # Create new orchestrator and manager with auto_resume disabled
    new_orchestrator = TrialOrchestrator(store=store)
    trial_manager = TrialManager(
        orchestrator=new_orchestrator,
        auto_resume=False,
    )

    await trial_manager.start()

    import asyncio

    await asyncio.sleep(0.1)

    # Trial should NOT be queued
    queued_trial = trial_manager.get_status("trial-no-auto-resume")
    assert queued_trial is None

    await trial_manager.stop()


@pytest.mark.asyncio
async def test_trial_manager_skips_trials_without_checkpoint(tmp_path) -> None:
    """Test that trials without checkpoints are marked as failed, not resumed."""
    store = FileSystemOrchestratorStore(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-no-checkpoint")

    # Launch but DON'T checkpoint
    await orchestrator.launch_trial(spec)
    status = orchestrator.get_trial_status("trial-no-checkpoint")
    assert status.phase is TrialPhase.RUNNING

    # Create new orchestrator and manager
    new_orchestrator = TrialOrchestrator(store=store)
    trial_manager = TrialManager(
        orchestrator=new_orchestrator,
        auto_resume=True,
    )

    await trial_manager.start()

    import asyncio

    await asyncio.sleep(0.1)

    # Trial should NOT be queued (no checkpoint)
    queued_trial = trial_manager.get_status("trial-no-checkpoint")
    assert queued_trial is None

    # But the trial status should be updated to FAILED in store
    record = store.get_trial_record("trial-no-checkpoint")
    assert record is not None
    assert record.last_status is not None
    assert record.last_status.phase is TrialPhase.FAILED
    assert "cannot resume" in (record.last_status.last_error or "").lower()

    await trial_manager.stop()


@pytest.mark.asyncio
async def test_trial_manager_skips_completed_trials(tmp_path) -> None:
    """Test that completed/stopped trials are not resumed."""
    store = FileSystemOrchestratorStore(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-completed")

    # Launch, checkpoint, and properly stop
    await orchestrator.launch_trial(spec)
    await orchestrator.checkpoint_trial("trial-completed")
    await orchestrator.stop_trial("trial-completed")

    status = orchestrator.get_trial_status("trial-completed")
    assert status.phase is TrialPhase.STOPPED

    # Create new orchestrator and manager
    new_orchestrator = TrialOrchestrator(store=store)
    trial_manager = TrialManager(
        orchestrator=new_orchestrator,
        auto_resume=True,
    )

    await trial_manager.start()

    import asyncio

    await asyncio.sleep(0.1)

    # Stopped trial should NOT be resumed
    queued_trial = trial_manager.get_status("trial-completed")
    assert queued_trial is None

    await trial_manager.stop()


@pytest.mark.asyncio
async def test_trial_spec_fields_preserved_through_persistence(tmp_path) -> None:
    """Test that all TrialSpec fields are preserved through normalization and persistence.

    This test verifies that _normalize_spec() and serialization/deserialization
    preserve all important spec fields. It catches bugs where new fields are added
    to TrialSpec but not copied in _normalize_spec() or _spec_with_resume_state().
    """
    store = FileSystemOrchestratorStore(tmp_path)
    orchestrator = TrialOrchestrator(store=store)

    # Create spec with all fields populated
    spec = _build_trial_spec("trial-spec-fields")
    spec.builder_name = "test_builder"

    # Verify original spec structure
    assert spec.trial_id == "trial-spec-fields"
    assert spec.builder_name == "test_builder"
    assert len(spec.operators) == 1
    assert len(spec.agents) == 1
    assert len(spec.data_streams) == 1
    original_operator_id = spec.operators[0].actor_id
    original_agent_id = spec.agents[0].actor_id
    original_stream_id = spec.data_streams[0].actor_id

    # Launch trial - this calls _normalize_spec() internally
    await orchestrator.launch_trial(spec)

    # Verify spec was saved correctly after normalization
    record = store.get_trial_record("trial-spec-fields")
    assert record is not None
    saved_spec = record.spec

    # Check all fields are preserved
    assert saved_spec.trial_id == "trial-spec-fields"
    assert saved_spec.builder_name == "test_builder"
    assert saved_spec.metadata == spec.metadata
    assert len(saved_spec.operators) == 1
    assert len(saved_spec.agents) == 1
    assert len(saved_spec.data_streams) == 1
    assert saved_spec.operators[0].actor_id == original_operator_id
    assert saved_spec.agents[0].actor_id == original_agent_id
    assert saved_spec.data_streams[0].actor_id == original_stream_id
    # resume fields are intentionally cleared by _normalize_spec
    assert saved_spec.resume_from_checkpoint_id is None
    assert saved_spec.resume_from_latest is False

    # Checkpoint and stop
    checkpoint = await orchestrator.checkpoint_trial("trial-spec-fields")
    await orchestrator.stop_trial("trial-spec-fields")

    # Create new orchestrator to simulate server restart
    new_orchestrator = TrialOrchestrator(store=store)

    # Load record from disk and verify all fields are preserved
    loaded_record = store.get_trial_record("trial-spec-fields")
    assert loaded_record is not None
    loaded_spec = loaded_record.spec

    assert loaded_spec.trial_id == "trial-spec-fields"
    assert loaded_spec.builder_name == "test_builder"
    assert loaded_spec.metadata == spec.metadata
    assert len(loaded_spec.operators) == 1
    assert len(loaded_spec.agents) == 1
    assert len(loaded_spec.data_streams) == 1
    assert loaded_spec.operators[0].actor_id == original_operator_id
    assert loaded_spec.agents[0].actor_id == original_agent_id
    assert loaded_spec.data_streams[0].actor_id == original_stream_id

    # Resume trial - this calls _spec_with_resume_state() internally
    await new_orchestrator.resume_trial(
        "trial-spec-fields", checkpoint_id=checkpoint.checkpoint_id
    )
    status = new_orchestrator.get_trial_status("trial-spec-fields")
    assert status.phase is TrialPhase.RUNNING

    # Verify spec is still correct after resume
    resumed_record = store.get_trial_record("trial-spec-fields")
    assert resumed_record is not None
    assert resumed_record.spec.builder_name == "test_builder"

    # Clean up
    await new_orchestrator.stop_trial("trial-spec-fields")


@pytest.mark.asyncio
async def test_complete_trial_updates_orchestrator_when_task_done(tmp_path) -> None:
    """Test that complete_trial() updates orchestrator status even when task is already done.

    This regression test covers the bug where complete_trial() only called
    orchestrator.stop_trial() inside an `if task and not task.done()` block,
    meaning trials whose background task completed naturally (e.g., game ended)
    would not have their orchestrator status updated.
    """
    store = FileSystemOrchestratorStore(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-complete-sync")

    # Launch the trial
    await orchestrator.launch_trial(spec)
    status = orchestrator.get_trial_status("trial-complete-sync")
    assert status.phase is TrialPhase.RUNNING

    # Create trial manager and submit the trial
    trial_manager = TrialManager(orchestrator=orchestrator, auto_resume=False)
    await trial_manager.start()

    # Manually add the trial to the manager as if it was submitted and running
    from dojozero.dashboard_server._trial_manager import QueuedTrial, QueuedTrialPhase

    queued = QueuedTrial(
        trial_id="trial-complete-sync",
        spec=spec,
        phase=QueuedTrialPhase.RUNNING,
    )
    trial_manager._trials["trial-complete-sync"] = queued

    # Create a completed task to simulate the trial's background task finishing naturally
    async def completed_coro() -> None:
        pass

    completed_task = asyncio.create_task(completed_coro())
    await completed_task  # Let it complete
    assert completed_task.done()

    # Add the completed task to _running_tasks
    trial_manager._running_tasks["trial-complete-sync"] = completed_task

    # Now call complete_trial() - this should still update the orchestrator
    result = await trial_manager.complete_trial("trial-complete-sync")
    assert result is True

    # Verify the orchestrator status is now STOPPED (not still RUNNING)
    status = orchestrator.get_trial_status("trial-complete-sync")
    assert status.phase is TrialPhase.STOPPED, (
        f"Expected STOPPED but got {status.phase}. "
        "complete_trial() should call orchestrator.stop_trial() even when task is done."
    )

    # Clean up
    await trial_manager.stop()


# =============================================================================
# Integration Tests: Status Consistency across ScheduleManager, TrialManager, Orchestrator
# =============================================================================


@pytest.mark.asyncio
async def test_integration_trial_completion_status_consistency(tmp_path) -> None:
    """Test that trial completion updates status consistently across all components.

    Scenario: A game ends normally
    Flow: ScheduleManager._stop_trial() → TrialManager.complete_trial() → Orchestrator.stop_trial()

    Assert:
      - ScheduleManager: phase=COMPLETED
      - TrialManager: phase=COMPLETED
      - Orchestrator: phase=STOPPED
    """
    from datetime import datetime, timezone

    store = FileSystemOrchestratorStore(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-integration-complete")

    # 1. Launch trial via orchestrator
    await orchestrator.launch_trial(spec)
    assert (
        orchestrator.get_trial_status("trial-integration-complete").phase
        is TrialPhase.RUNNING
    )

    # 2. Set up TrialManager tracking this trial
    trial_manager = TrialManager(orchestrator=orchestrator, auto_resume=False)
    await trial_manager.start()

    # Manually add the trial as if it was submitted and running
    queued = QueuedTrial(
        trial_id="trial-integration-complete",
        spec=spec,
        phase=QueuedTrialPhase.RUNNING,
    )
    trial_manager._trials["trial-integration-complete"] = queued

    # Create a completed task (simulates game ending naturally)
    async def completed_coro() -> None:
        pass

    completed_task = asyncio.create_task(completed_coro())
    await completed_task
    trial_manager._running_tasks["trial-integration-complete"] = completed_task

    # 3. Set up ScheduleManager with a scheduled trial pointing to this trial
    schedule_manager = ScheduleManager(
        trial_manager=trial_manager,
        store=None,  # No persistence needed for test
    )

    now = datetime.now(timezone.utc)
    scheduled = ScheduledTrial(
        schedule_id="sched-test-001",
        scenario_name="test",
        scenario_config={},
        sport_type="nba",
        game_id="001",
        event_time=now,
        scheduled_start_time=now,
        pre_start_hours=2.0,
        check_interval_seconds=60.0,
        auto_stop_on_completion=True,
        phase=ScheduledTrialPhase.RUNNING,
        created_at=now,
        launched_trial_id="trial-integration-complete",
    )
    schedule_manager._schedules["sched-test-001"] = scheduled

    # 4. Simulate game completion by calling _stop_trial
    await schedule_manager._stop_trial(scheduled)

    # 5. Verify status consistency across all components
    # ScheduleManager should show COMPLETED
    assert scheduled.phase == ScheduledTrialPhase.COMPLETED, (
        f"ScheduleManager expected COMPLETED but got {scheduled.phase}"
    )

    # TrialManager should show COMPLETED
    queued_status = trial_manager.get_status("trial-integration-complete")
    assert queued_status is not None
    assert queued_status.phase == QueuedTrialPhase.COMPLETED, (
        f"TrialManager expected COMPLETED but got {queued_status.phase}"
    )

    # Orchestrator should show STOPPED
    orch_status = orchestrator.get_trial_status("trial-integration-complete")
    assert orch_status.phase is TrialPhase.STOPPED, (
        f"Orchestrator expected STOPPED but got {orch_status.phase}"
    )

    # Clean up
    await trial_manager.stop()


@pytest.mark.asyncio
async def test_integration_user_cancellation_status_consistency(tmp_path) -> None:
    """Test that user cancellation updates status consistently across all components.

    Scenario: User cancels a running trial via API
    Flow: ScheduleManager.cancel_scheduled() → TrialManager.cancel() → Orchestrator.stop_trial()

    Assert:
      - ScheduleManager: phase=CANCELLED
      - TrialManager: phase=CANCELLED
      - Orchestrator: phase=STOPPED
    """
    from datetime import datetime, timezone

    store = FileSystemOrchestratorStore(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-integration-cancel")

    # 1. Launch trial via orchestrator
    await orchestrator.launch_trial(spec)
    assert (
        orchestrator.get_trial_status("trial-integration-cancel").phase
        is TrialPhase.RUNNING
    )

    # 2. Set up TrialManager tracking this trial
    trial_manager = TrialManager(orchestrator=orchestrator, auto_resume=False)
    await trial_manager.start()

    # Manually add the trial as if it was submitted and running
    queued = QueuedTrial(
        trial_id="trial-integration-cancel",
        spec=spec,
        phase=QueuedTrialPhase.RUNNING,
    )
    trial_manager._trials["trial-integration-cancel"] = queued

    # Create a running task (not completed yet)
    async def long_running_coro() -> None:
        await asyncio.sleep(3600)  # Would run forever, but we'll cancel it

    running_task = asyncio.create_task(long_running_coro())
    trial_manager._running_tasks["trial-integration-cancel"] = running_task

    # 3. Set up ScheduleManager with a scheduled trial pointing to this trial
    schedule_manager = ScheduleManager(
        trial_manager=trial_manager,
        store=None,
    )

    now = datetime.now(timezone.utc)
    scheduled = ScheduledTrial(
        schedule_id="sched-cancel-001",
        scenario_name="test",
        scenario_config={},
        sport_type="nba",
        game_id="001",
        event_time=now,
        scheduled_start_time=now,
        pre_start_hours=2.0,
        check_interval_seconds=60.0,
        auto_stop_on_completion=True,
        phase=ScheduledTrialPhase.RUNNING,
        created_at=now,
        launched_trial_id="trial-integration-cancel",
    )
    schedule_manager._schedules["sched-cancel-001"] = scheduled

    # 4. User cancels via ScheduleManager
    result = await schedule_manager.cancel_scheduled("sched-cancel-001")
    assert result is True

    # 5. Verify status consistency across all components
    # ScheduleManager should show CANCELLED
    updated_scheduled = schedule_manager.get_scheduled("sched-cancel-001")
    assert updated_scheduled is not None
    assert updated_scheduled.phase == ScheduledTrialPhase.CANCELLED, (
        f"ScheduleManager expected CANCELLED but got {updated_scheduled.phase}"
    )

    # TrialManager should show CANCELLED
    queued_status = trial_manager.get_status("trial-integration-cancel")
    assert queued_status is not None
    assert queued_status.phase == QueuedTrialPhase.CANCELLED, (
        f"TrialManager expected CANCELLED but got {queued_status.phase}"
    )

    # Orchestrator should show STOPPED
    orch_status = orchestrator.get_trial_status("trial-integration-cancel")
    assert orch_status.phase is TrialPhase.STOPPED, (
        f"Orchestrator expected STOPPED but got {orch_status.phase}"
    )

    # Clean up
    await trial_manager.stop()


@pytest.mark.asyncio
async def test_integration_server_restart_recovery(tmp_path) -> None:
    """Test that server restart recovers trial status consistently.

    Scenario: Server crashes while a scheduled trial is running, then restarts.
    The TrialManager auto-resumes the trial, and when the game ends,
    the ScheduleManager should be able to complete it properly.

    This test verifies:
    1. TrialManager auto-resumes the trial on restart
    2. When game ends, ScheduleManager._stop_trial() works correctly
    3. All components have consistent status after recovery and completion
    """
    from datetime import datetime, timezone
    from dojozero.dashboard_server._scheduler import FileSchedulerStore

    # === PHASE 1: Initial server session (before crash) ===
    store = FileSystemOrchestratorStore(tmp_path)
    scheduler_store = FileSchedulerStore(tmp_path / "schedules.json")
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-restart-test")

    # Launch trial and create checkpoint (required for auto-resume)
    await orchestrator.launch_trial(spec)
    await orchestrator.checkpoint_trial("trial-restart-test")
    assert (
        orchestrator.get_trial_status("trial-restart-test").phase is TrialPhase.RUNNING
    )

    # Create scheduled trial entry and persist it
    now = datetime.now(timezone.utc)
    scheduled = ScheduledTrial(
        schedule_id="sched-restart-001",
        scenario_name="test",
        scenario_config={},
        sport_type="nba",
        game_id="001",
        event_time=now,
        scheduled_start_time=now,
        pre_start_hours=2.0,
        check_interval_seconds=60.0,
        auto_stop_on_completion=True,
        phase=ScheduledTrialPhase.RUNNING,
        created_at=now,
        launched_trial_id="trial-restart-test",
    )
    scheduler_store.save([scheduled])

    # Simulate server crash - don't call stop(), just abandon the orchestrator
    # The trial status is still RUNNING in the store

    # === PHASE 2: Server restart ===
    # Create new instances (simulating server restart)
    new_orchestrator = TrialOrchestrator(store=store)
    new_trial_manager = TrialManager(
        orchestrator=new_orchestrator,
        auto_resume=True,
        stale_threshold_hours=24.0,
    )

    # Start TrialManager - should auto-resume the interrupted trial
    await new_trial_manager.start()

    # Give a moment for async operations
    await asyncio.sleep(0.1)

    # Verify trial was auto-resumed in orchestrator
    orch_status = new_orchestrator.get_trial_status("trial-restart-test")
    assert orch_status.phase is TrialPhase.RUNNING, (
        f"Expected orchestrator to show RUNNING after auto-resume, got {orch_status.phase}"
    )

    # Create new ScheduleManager and load persisted schedules
    new_schedule_manager = ScheduleManager(
        trial_manager=new_trial_manager,
        store=scheduler_store,
    )
    await new_schedule_manager.start()

    # Verify scheduled trial was loaded
    loaded_scheduled = new_schedule_manager.get_scheduled("sched-restart-001")
    assert loaded_scheduled is not None
    assert loaded_scheduled.phase == ScheduledTrialPhase.RUNNING

    # === PHASE 3: Game ends - test completion flow ===
    # Note: The trial was auto-resumed by TrialManager directly via orchestrator,
    # so it's NOT in new_trial_manager._trials dict. This is the tricky part.

    # Simulate game completion by calling _stop_trial
    await new_schedule_manager._stop_trial(loaded_scheduled)

    # Verify status consistency after completion
    # ScheduleManager should show COMPLETED
    final_scheduled = new_schedule_manager.get_scheduled("sched-restart-001")
    assert final_scheduled is not None
    assert final_scheduled.phase == ScheduledTrialPhase.COMPLETED, (
        f"ScheduleManager expected COMPLETED but got {final_scheduled.phase}"
    )

    # Orchestrator should show STOPPED
    final_orch_status = new_orchestrator.get_trial_status("trial-restart-test")
    assert final_orch_status.phase is TrialPhase.STOPPED, (
        f"Orchestrator expected STOPPED but got {final_orch_status.phase}"
    )

    # Clean up
    await new_schedule_manager.stop()
    await new_trial_manager.stop()


@pytest.mark.asyncio
async def test_integration_complete_trial_already_stopped(tmp_path) -> None:
    """Test that complete_trial() handles already-stopped trials gracefully.

    Scenario: A trial was stopped outside of TrialManager (e.g., manually via orchestrator),
    and then ScheduleManager tries to complete it when the game ends.

    This should return True (trial is in terminal state) without errors.
    """
    from datetime import datetime, timezone

    store = FileSystemOrchestratorStore(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_trial_spec("trial-already-stopped")

    # Launch and immediately stop the trial via orchestrator
    await orchestrator.launch_trial(spec)
    await orchestrator.stop_trial("trial-already-stopped")
    assert (
        orchestrator.get_trial_status("trial-already-stopped").phase
        is TrialPhase.STOPPED
    )

    # Set up TrialManager (trial is NOT tracked here)
    trial_manager = TrialManager(orchestrator=orchestrator, auto_resume=False)
    await trial_manager.start()

    # Set up ScheduleManager with a scheduled trial pointing to this trial
    schedule_manager = ScheduleManager(
        trial_manager=trial_manager,
        store=None,
    )

    now = datetime.now(timezone.utc)
    scheduled = ScheduledTrial(
        schedule_id="sched-already-stopped-001",
        scenario_name="test",
        scenario_config={},
        sport_type="nba",
        game_id="001",
        event_time=now,
        scheduled_start_time=now,
        pre_start_hours=2.0,
        check_interval_seconds=60.0,
        auto_stop_on_completion=True,
        phase=ScheduledTrialPhase.RUNNING,
        created_at=now,
        launched_trial_id="trial-already-stopped",
    )
    schedule_manager._schedules["sched-already-stopped-001"] = scheduled

    # Simulate game completion - should handle already-stopped trial gracefully
    await schedule_manager._stop_trial(scheduled)

    # ScheduleManager should show COMPLETED (even though trial was already stopped)
    assert scheduled.phase == ScheduledTrialPhase.COMPLETED, (
        f"ScheduleManager expected COMPLETED but got {scheduled.phase}"
    )

    # Orchestrator should still show STOPPED
    orch_status = orchestrator.get_trial_status("trial-already-stopped")
    assert orch_status.phase is TrialPhase.STOPPED

    # Clean up
    await trial_manager.stop()
