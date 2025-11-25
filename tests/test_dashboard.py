from typing import Any, Mapping, Sequence, TypedDict

import pytest

from agentx.core import (
    ActorBase,
    ActorRuntimeContext,
    ActorSpec,
    Agent,
    Dashboard,
    DataStream,
    DataStreamBase,
    FileSystemDashboardStore,
    InMemoryDashboardStore,
    Operator,
    StreamEvent,
    TrialPhase,
    TrialSpec,
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
    consumers: Sequence[str]


class DummyOperator(ActorBase, Operator[DummyOperatorConfig]):
    def __init__(self, actor_id: str) -> None:
        super().__init__(actor_id)
        self.events_handled = 0
        self.restored_events = 0

    @classmethod
    def from_dict(
        cls,
        config: DummyOperatorConfig,
        *,
        context: ActorRuntimeContext | None = None,
    ) -> "DummyOperator":
        del context
        return cls(actor_id=str(config["actor_id"]))

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


class DummyAgent(ActorBase, Agent[DummyAgentConfig]):
    def __init__(self, actor_id: str) -> None:
        super().__init__(actor_id)
        self.event_count = 0
        self.restored_count = 0

    @classmethod
    def from_dict(
        cls,
        config: DummyAgentConfig,
        *,
        context: ActorRuntimeContext | None = None,
    ) -> "DummyAgent":
        del context
        return cls(actor_id=str(config["actor_id"]))

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
    def __init__(
        self,
        actor_id: str,
        *,
        consumers: Sequence[Agent | Operator] | None = None,
    ) -> None:
        super().__init__(actor_id, consumers=consumers)
        self.emitted = 0
        self.restored_emissions = 0

    @classmethod
    def from_dict(
        cls,
        config: DummyStreamConfig,
        *,
        context: ActorRuntimeContext | None = None,
    ) -> "DummyDataStream":
        consumer_ids = tuple(config.get("consumers", ()))
        if consumer_ids and context is None:
            raise RuntimeError("DummyDataStream requires runtime context")
        consumers: list[Agent | Operator] = []
        if context is not None:
            for consumer_id in consumer_ids:
                handle: Agent | Operator | None = context.agents.get(consumer_id)
                if handle is None:
                    handle = context.operators.get(consumer_id)
                if handle is None:
                    raise RuntimeError(
                        f"DummyDataStream could not resolve consumer '{consumer_id}'"
                    )
                consumers.append(handle)
        return cls(actor_id=str(config["actor_id"]), consumers=tuple(consumers))

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
async def test_dashboard_lifecycle_order() -> None:
    store = InMemoryDashboardStore()
    dashboard = Dashboard(store=store)
    spec = _build_trial_spec("trial-lifecycle")

    await dashboard.launch_trial(spec)

    status = dashboard.get_trial_status("trial-lifecycle")
    assert status.phase is TrialPhase.RUNNING

    stream = dashboard.get_actor("trial-lifecycle", "stream-1")
    assert isinstance(stream, DummyDataStream)
    assert set(stream.consumers) == {"op-1", "agent-1"}

    await dashboard.stop_trial("trial-lifecycle")

    status = dashboard.get_trial_status("trial-lifecycle")
    assert status.phase is TrialPhase.STOPPED
    # Ensure persisted status is visible after a new dashboard instance is created.
    replacement_dashboard = Dashboard(store=store)
    status = replacement_dashboard.get_trial_status("trial-lifecycle")
    assert status.phase is TrialPhase.STOPPED
    assert replacement_dashboard.list_trials()[0].trial_id == "trial-lifecycle"

    assert CALL_LOG == [
        ("op-1", "start"),
        ("agent-1", "start"),
        ("stream-1", "start"),
        ("stream-1", "stop"),
        ("agent-1", "stop"),
        ("op-1", "stop"),
    ]


@pytest.mark.asyncio
async def test_dashboard_checkpoint_and_resume() -> None:
    store = InMemoryDashboardStore()
    dashboard = Dashboard(store=store)
    spec = _build_trial_spec("trial-checkpoint")

    await dashboard.launch_trial(spec)

    stream = dashboard.get_actor("trial-checkpoint", "stream-1")
    assert isinstance(stream, DummyDataStream)
    await stream.emit({"price": 42})

    checkpoint = await dashboard.checkpoint_trial("trial-checkpoint")
    agent_state = checkpoint.actor_states["agent-1"]
    assert agent_state["events"] == 1
    assert checkpoint.checkpoint_id is not None
    summaries = dashboard.list_checkpoints("trial-checkpoint")
    assert summaries[-1].checkpoint_id == checkpoint.checkpoint_id

    await dashboard.stop_trial("trial-checkpoint")

    resumed_dashboard = Dashboard(store=store)
    status = await resumed_dashboard.resume_trial(
        "trial-checkpoint", checkpoint_id=checkpoint.checkpoint_id
    )
    assert status.phase is TrialPhase.RUNNING

    resumed_agent = resumed_dashboard.get_actor("trial-checkpoint", "agent-1")
    assert isinstance(resumed_agent, DummyAgent)
    assert resumed_agent.event_count == 1

    resumed_operator = resumed_dashboard.get_actor("trial-checkpoint", "op-1")
    assert isinstance(resumed_operator, DummyOperator)
    assert resumed_operator.events_handled == 1

    resumed_stream = resumed_dashboard.get_actor("trial-checkpoint", "stream-1")
    assert isinstance(resumed_stream, DummyDataStream)
    assert resumed_stream.restored_emissions == 1

    await resumed_dashboard.stop_trial("trial-checkpoint")
    await resumed_dashboard.delete_trial("trial-checkpoint")


@pytest.mark.asyncio
async def test_filesystem_dashboard_store_persistence(tmp_path) -> None:
    store = FileSystemDashboardStore(tmp_path)
    dashboard = Dashboard(store=store)
    spec = _build_trial_spec("trial-fs")

    await dashboard.launch_trial(spec)
    stream = dashboard.get_actor("trial-fs", "stream-1")
    assert isinstance(stream, DummyDataStream)
    await stream.emit({"price": 7})
    checkpoint = await dashboard.checkpoint_trial("trial-fs")
    await dashboard.stop_trial("trial-fs")

    restored_dashboard = Dashboard(store=store)
    checkpoints = restored_dashboard.list_checkpoints("trial-fs")
    assert checkpoints
    assert checkpoints[-1].checkpoint_id == checkpoint.checkpoint_id
    status = restored_dashboard.get_trial_status("trial-fs")
    assert status.phase is TrialPhase.STOPPED

    resumed_status = await restored_dashboard.resume_trial("trial-fs")
    assert resumed_status.phase is TrialPhase.RUNNING
    resumed_agent = restored_dashboard.get_actor("trial-fs", "agent-1")
    assert isinstance(resumed_agent, DummyAgent)
    assert resumed_agent.event_count == 1

    await restored_dashboard.stop_trial("trial-fs")
    await restored_dashboard.delete_trial("trial-fs")
    assert restored_dashboard.list_trials() == ()


@pytest.mark.asyncio
async def test_launch_trial_uses_spec_resume_checkpoint() -> None:
    store = InMemoryDashboardStore()
    dashboard = Dashboard(store=store)
    spec = _build_trial_spec("trial-resume-spec")

    await dashboard.launch_trial(spec)
    stream = dashboard.get_actor("trial-resume-spec", "stream-1")
    assert isinstance(stream, DummyDataStream)
    await stream.emit({"price": 101})
    checkpoint = await dashboard.checkpoint_trial("trial-resume-spec")
    await dashboard.stop_trial("trial-resume-spec")

    resumed_dashboard = Dashboard(store=store)
    resumed_spec = _build_trial_spec("trial-resume-spec")
    resumed_spec.resume_from_checkpoint_id = checkpoint.checkpoint_id
    status = await resumed_dashboard.launch_trial(resumed_spec)
    assert status.phase is TrialPhase.RUNNING

    resumed_agent = resumed_dashboard.get_actor("trial-resume-spec", "agent-1")
    assert isinstance(resumed_agent, DummyAgent)
    assert resumed_agent.event_count == 1

    await resumed_dashboard.stop_trial("trial-resume-spec")


def _build_trial_spec(
    trial_id: str,
    *,
    resume_states: Mapping[str, Mapping[str, Any]] | None = None,
) -> TrialSpec:
    resume_states = resume_states or {}
    operator_config: DummyOperatorConfig = {"actor_id": "op-1"}
    agent_config: DummyAgentConfig = {"actor_id": "agent-1"}
    stream_config: DummyStreamConfig = {
        "actor_id": "stream-1",
        "consumers": ("op-1", "agent-1"),
    }
    return TrialSpec(
        trial_id=trial_id,
        operators=(
            ActorSpec(
                actor_id="op-1",
                actor_cls=DummyOperator,
                config=operator_config,
                resume_state=resume_states.get("op-1"),
            ),
        ),
        agents=(
            ActorSpec(
                actor_id="agent-1",
                actor_cls=DummyAgent,
                config=agent_config,
                resume_state=resume_states.get("agent-1"),
            ),
        ),
        data_streams=(
            ActorSpec(
                actor_id="stream-1",
                actor_cls=DummyDataStream,
                config=stream_config,
                resume_state=resume_states.get("stream-1"),
            ),
        ),
        metadata={"env": "test"},
    )
