import asyncio
from pathlib import Path
from typing import Callable, cast
from uuid import uuid4

import pytest
import ray

from dojozero.core import (
    TrialOrchestrator,
    OrchestratorStore,
    FileSystemOrchestratorStore,
    InMemoryOrchestratorStore,
    get_trial_builder_definition,
    TrialPhase,
)
from dojozero.samples.bounded_random import (
    BoundedRandomStringDataStream,
    CounterAgent,
    CounterOperator,
    BoundedRandomTrialParams,
)
from dojozero.ray_runtime import RayActorRuntimeProvider

StoreBuilder = Callable[[Path], OrchestratorStore]


def _build_bounded_random_spec(trial_id: str, config: BoundedRandomTrialParams):
    builder = get_trial_builder_definition("samples.bounded-random")
    return builder.build(trial_id, config.model_dump(mode="python"))


def _memory_store(_: Path) -> OrchestratorStore:
    return InMemoryOrchestratorStore()


def _filesystem_store(tmp_path: Path) -> OrchestratorStore:
    store_dir = tmp_path / "fs-store"
    store_dir.mkdir(parents=True, exist_ok=True)
    return FileSystemOrchestratorStore(store_dir)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "store_builder",
    [
        pytest.param(_memory_store, id="memory"),
        pytest.param(_filesystem_store, id="filesystem"),
    ],
)
async def test_bounded_random_runs_end_to_end(
    store_builder: StoreBuilder, tmp_path: Path
) -> None:
    store = store_builder(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_bounded_random_spec(
        "sample-trial",
        BoundedRandomTrialParams(
            total_events=5,
            payload_length=6,
            interval_seconds=0.0,
            seed=1234,
        ),
    )

    status = await orchestrator.launch_trial(spec)
    assert status.phase is TrialPhase.RUNNING

    stream = orchestrator.get_actor("sample-trial", "random-stream")
    assert isinstance(stream, BoundedRandomStringDataStream)
    stream = cast(BoundedRandomStringDataStream, stream)
    await asyncio.wait_for(stream.wait_until_finished(), timeout=2)

    agent = orchestrator.get_actor("sample-trial", "counter-agent")
    operator = orchestrator.get_actor("sample-trial", "counter-operator")
    assert isinstance(agent, CounterAgent)
    assert isinstance(operator, CounterOperator)
    agent = cast(CounterAgent, agent)
    operator = cast(CounterOperator, operator)

    assert agent.events_processed == 5
    assert operator.value == 5
    assert stream.emitted == 5

    await orchestrator.stop_trial("sample-trial")
    status = orchestrator.get_trial_status("sample-trial")
    assert status.phase is TrialPhase.STOPPED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "store_builder",
    [
        pytest.param(_memory_store, id="memory"),
        pytest.param(_filesystem_store, id="filesystem"),
    ],
)
async def test_bounded_random_checkpoint_resume(
    store_builder: StoreBuilder, tmp_path: Path
) -> None:
    store = store_builder(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_bounded_random_spec(
        "sample-resume",
        BoundedRandomTrialParams(
            total_events=3,
            interval_seconds=0.0,
            seed=4321,
        ),
    )

    await orchestrator.launch_trial(spec)
    stream = orchestrator.get_actor("sample-resume", "random-stream")
    assert isinstance(stream, BoundedRandomStringDataStream)
    stream = cast(BoundedRandomStringDataStream, stream)
    await asyncio.wait_for(stream.wait_until_finished(), timeout=2)

    checkpoint = await orchestrator.checkpoint_trial("sample-resume")
    assert checkpoint.checkpoint_id is not None

    await orchestrator.stop_trial("sample-resume")

    resumed_orchestrator = TrialOrchestrator(store=store)
    resumed_status = await resumed_orchestrator.resume_trial(
        "sample-resume", checkpoint_id=checkpoint.checkpoint_id
    )
    assert resumed_status.phase is TrialPhase.RUNNING

    resumed_agent = resumed_orchestrator.get_actor("sample-resume", "counter-agent")
    resumed_operator = resumed_orchestrator.get_actor(
        "sample-resume", "counter-operator"
    )
    assert isinstance(resumed_agent, CounterAgent)
    assert isinstance(resumed_operator, CounterOperator)
    resumed_agent = cast(CounterAgent, resumed_agent)
    resumed_operator = cast(CounterOperator, resumed_operator)
    assert resumed_agent.events_processed == 3
    assert resumed_operator.value == 3

    await resumed_orchestrator.stop_trial("sample-resume")
    await resumed_orchestrator.delete_trial("sample-resume")


@pytest.mark.asyncio
async def test_bounded_random_runs_with_ray_runtime(tmp_path: Path) -> None:
    if ray.is_initialized():
        ray.shutdown()
    namespace = f"dojozero-test-{uuid4().hex}"
    provider = RayActorRuntimeProvider(
        init_kwargs={"namespace": namespace, "ignore_reinit_error": True}
    )
    store = InMemoryOrchestratorStore()
    orchestrator = TrialOrchestrator(store=store, runtime_provider=provider)
    spec = _build_bounded_random_spec(
        "sample-ray",
        BoundedRandomTrialParams(
            total_events=3,
            interval_seconds=0.0,
            seed=9876,
        ),
    )

    try:
        status = await orchestrator.launch_trial(spec)
        assert status.phase is TrialPhase.RUNNING

        stream = orchestrator.get_actor("sample-ray", "random-stream")
        stream = cast(BoundedRandomStringDataStream, stream)
        await asyncio.wait_for(stream.wait_until_finished(), timeout=5)

        checkpoint = await orchestrator.checkpoint_trial("sample-ray")
        agent_state = checkpoint.actor_states["counter-agent"]
        assert agent_state["events"] == 3

        await orchestrator.stop_trial("sample-ray")
    finally:
        if ray.is_initialized():
            ray.shutdown()
