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
    TrialPhase,
    get_trial_builder_definition,
)
from dojozero.samples.bounded_random import (
    BoundedRandomStringDataStream,
    CounterOperator,
)
from dojozero.samples.bounded_random_buffered import (
    BoundedRandomBufferedTrialParams,
    CounterAgentBuffered,
)
from dojozero.ray_runtime import RayActorRuntimeProvider

StoreBuilder = Callable[[Path], OrchestratorStore]


def _build_bounded_random_buffered_spec(
    trial_id: str, config: BoundedRandomBufferedTrialParams
):
    builder = get_trial_builder_definition("samples.bounded-random-buffered")
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
async def test_bounded_random_buffered_flushes_events(
    store_builder: StoreBuilder, tmp_path: Path
) -> None:
    store = store_builder(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    spec = _build_bounded_random_buffered_spec(
        "buffered-trial",
        BoundedRandomBufferedTrialParams(
            total_events=6,
            payload_length=4,
            interval_seconds=0.0,
            seed=2468,
            buffer_flush_seconds=0.05,
        ),
    )

    status = await orchestrator.launch_trial(spec)
    assert status.phase is TrialPhase.RUNNING

    stream = orchestrator.get_actor("buffered-trial", "random-stream")
    assert isinstance(stream, BoundedRandomStringDataStream)
    stream = cast(BoundedRandomStringDataStream, stream)
    await asyncio.wait_for(stream.wait_until_finished(), timeout=2)

    await asyncio.sleep(0.2)

    agent = orchestrator.get_actor("buffered-trial", "counter-agent-buffered")
    operator = orchestrator.get_actor("buffered-trial", "counter-operator")
    assert isinstance(agent, CounterAgentBuffered)
    assert isinstance(operator, CounterOperator)
    operator = cast(CounterOperator, operator)

    assert operator.value == 6
    assert stream.emitted == 6

    await orchestrator.stop_trial("buffered-trial")
    status = orchestrator.get_trial_status("buffered-trial")
    assert status.phase is TrialPhase.STOPPED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "store_builder",
    [
        pytest.param(_memory_store, id="memory"),
        pytest.param(_filesystem_store, id="filesystem"),
    ],
)
async def test_bounded_random_buffered_checkpoint_resume(
    store_builder: StoreBuilder, tmp_path: Path
) -> None:
    store = store_builder(tmp_path)
    orchestrator = TrialOrchestrator(store=store)
    total_events = 4
    spec = _build_bounded_random_buffered_spec(
        "buffered-resume",
        BoundedRandomBufferedTrialParams(
            total_events=total_events,
            interval_seconds=0.0,
            seed=1357,
            buffer_flush_seconds=0.05,
        ),
    )

    await orchestrator.launch_trial(spec)
    stream = orchestrator.get_actor("buffered-resume", "random-stream")
    stream = cast(BoundedRandomStringDataStream, stream)
    await asyncio.wait_for(stream.wait_until_finished(), timeout=2)
    await asyncio.sleep(0.2)

    checkpoint = await orchestrator.checkpoint_trial("buffered-resume")
    assert checkpoint.checkpoint_id is not None

    await orchestrator.stop_trial("buffered-resume")

    resumed_orchestrator = TrialOrchestrator(store=store)
    resumed_status = await resumed_orchestrator.resume_trial(
        "buffered-resume", checkpoint_id=checkpoint.checkpoint_id
    )
    assert resumed_status.phase is TrialPhase.RUNNING

    resumed_operator = resumed_orchestrator.get_actor(
        "buffered-resume", "counter-operator"
    )
    assert isinstance(resumed_operator, CounterOperator)
    assert cast(CounterOperator, resumed_operator).value == total_events

    resumed_agent = resumed_orchestrator.get_actor(
        "buffered-resume", "counter-agent-buffered"
    )
    assert isinstance(resumed_agent, CounterAgentBuffered)
    assert getattr(resumed_agent, "_events") == total_events

    await resumed_orchestrator.stop_trial("buffered-resume")
    await resumed_orchestrator.delete_trial("buffered-resume")


@pytest.mark.asyncio
async def test_bounded_random_buffered_runs_with_ray_runtime(tmp_path: Path) -> None:
    if ray.is_initialized():
        ray.shutdown()
    namespace = f"dojozero-buffered-test-{uuid4().hex}"
    provider = RayActorRuntimeProvider(
        init_kwargs={"namespace": namespace, "ignore_reinit_error": True}
    )
    store = InMemoryOrchestratorStore()
    orchestrator = TrialOrchestrator(store=store, runtime_provider=provider)
    total_events = 3
    spec = _build_bounded_random_buffered_spec(
        "buffered-ray",
        BoundedRandomBufferedTrialParams(
            total_events=total_events,
            interval_seconds=0.0,
            seed=97531,
            buffer_flush_seconds=0.05,
        ),
    )

    try:
        status = await orchestrator.launch_trial(spec)
        assert status.phase is TrialPhase.RUNNING

        stream = orchestrator.get_actor("buffered-ray", "random-stream")
        stream = cast(BoundedRandomStringDataStream, stream)
        await asyncio.wait_for(stream.wait_until_finished(), timeout=5)
        await asyncio.sleep(0.2)

        checkpoint = await orchestrator.checkpoint_trial("buffered-ray")
        agent_state = checkpoint.actor_states["counter-agent-buffered"]
        assert agent_state["events"] == total_events

        await orchestrator.stop_trial("buffered-ray")
    finally:
        if ray.is_initialized():
            ray.shutdown()
