from typing import Any, Mapping, TypedDict

import pytest

from dojozero.core import (
    RuntimeContext,
    Agent,
    AgentBase,
    AgentSpec,
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
from dojozero.dashboard_server._trial_manager import TrialManager

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
) -> TrialSpec:
    resume_states = resume_states or {}
    operator_config: DummyOperatorConfig = {"actor_id": "op-1"}
    agent_config: DummyAgentConfig = {"actor_id": "agent-1"}
    stream_config: DummyStreamConfig = {"actor_id": "stream-1"}
    return TrialSpec(
        trial_id=trial_id,
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
        metadata={"env": "test"},
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
    # The trial is resumed directly via orchestrator.resume_trial(), not via the queue
    resumed_status = new_orchestrator.get_trial_status("trial-auto-resume")
    assert resumed_status.phase is TrialPhase.RUNNING

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
