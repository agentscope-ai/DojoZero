"""Reference scenario demonstrating how to wire DojoZero actors together.

Readers can treat this module as a cookbook for authoring new built-in
scenarios under ``dojozero``. It highlights the minimum surface to expose so
the CLI registry can discover your builder automatically:

1. **Serializable configs.** Each actor exposes a TypedDict configuration and a
    ``from_dict`` constructor so trials can be reconstructed from YAML specs or
    checkpoints. Stick to JSON-serializable values.
2. **Dependency wiring.** Trial specs declare dependent actor IDs so the
    dashboard can invoke the ``register_xxx`` hooks automatically. This sample
    shows how an agent validates its operator registration and how a stream fans
    out events to its consumers.
3. **Checkpoint-friendly state.** Every actor implements ``save_state`` and
    ``load_state`` with plain dictionaries so trials can pause/resume on both the
    local and Ray runtimes without bespoke persistence code.
4. **Trial assembly helper.** A private helper builds a ready-to-run
    :class:`TrialSpec`; ``register_trial_builder`` exposes it under
    ``samples.bounded-random`` so the CLI can validate configs and launch trials
    without importing this module directly.
"""

import asyncio
import logging
import random
import string
from typing import Any, Mapping, Protocol, Sequence, TypedDict, cast

from pydantic import BaseModel, Field

from dojozero.core import (
    Agent,
    AgentBase,
    AgentSpec,
    DataStream,
    DataStreamBase,
    DataStreamSpec,
    Operator,
    OperatorBase,
    OperatorSpec,
    register_trial_builder,
    StreamEvent,
    TrialSpec,
)

Alphabet = string.ascii_letters + string.digits
LOGGER = logging.getLogger("dojozero.samples.bounded_random")


def _preview_payload(value: Any, *, limit: int = 32) -> str:
    text = value if isinstance(value, str) else repr(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


class _ActorIdConfig(TypedDict):
    actor_id: str


class BoundedRandomStringDataStreamConfig(_ActorIdConfig, total=False):
    total_events: int
    payload_length: int
    interval_seconds: float
    seed: int


class CounterOperatorConfig(_ActorIdConfig):
    pass


class CounterAgentConfig(_ActorIdConfig):
    operator_id: str


class BoundedRandomTrialParams(BaseModel):
    total_events: int = Field(default=10, ge=0)
    payload_length: int = Field(default=8, gt=0)
    interval_seconds: float = Field(default=0.0, ge=0.0)
    seed: int | None = None
    stream_id: str = "random-stream"
    operator_id: str = "counter-operator"
    agent_id: str = "counter-agent"


class _CounterOperatorLike(Operator[CounterOperatorConfig], Protocol):
    # Only the Agent-specific RPC surface (``count``) is required; the
    # Operator lifecycle pieces are supplied by the base protocol.
    async def count(self) -> int: ...


class BoundedRandomStringDataStream(
    DataStreamBase, DataStream[BoundedRandomStringDataStreamConfig]
):
    """DataStream that emits a bounded number of random string payloads."""

    def __init__(
        self,
        *,
        actor_id: str,
        total_events: int,
        payload_length: int,
        interval_seconds: float,
        seed: int | None,
    ) -> None:
        super().__init__(actor_id)
        if total_events < 0:
            raise ValueError("total_events must be non-negative")
        if payload_length <= 0:
            raise ValueError("payload_length must be positive")
        self._total_events = total_events
        self._payload_length = payload_length
        self._interval_seconds = max(0.0, interval_seconds)
        self._seed = seed if seed is not None else random.randrange(0, 2**32)
        self._rng = random.Random(self._seed)
        self._emit_task: asyncio.Task[None] | None = None
        self._emitted = 0
        self._emission_complete = asyncio.Event()
        self._cancelled = False

    @property
    def emitted(self) -> int:
        return self._emitted

    async def wait_until_finished(self) -> None:
        await self._emission_complete.wait()

    @classmethod
    def from_dict(
        cls,
        config: BoundedRandomStringDataStreamConfig,
    ) -> "BoundedRandomStringDataStream":
        return cls(
            actor_id=config["actor_id"],
            total_events=config.get("total_events", 10),
            payload_length=config.get("payload_length", 8),
            interval_seconds=config.get("interval_seconds", 0.0),
            seed=config.get("seed"),
        )

    async def start(self) -> None:
        """Protocol hook: dashboard calls this before events begin flowing."""
        if self._emit_task is not None:
            return
        self._emission_complete.clear()
        self._cancelled = False
        LOGGER.info(
            "stream '%s' starting: emitted=%d total=%d interval=%s payload_length=%d",
            self.actor_id,
            self._emitted,
            self._total_events,
            self._interval_seconds,
            self._payload_length,
        )
        self._emit_task = asyncio.create_task(self._emit_events())

    async def stop(self) -> None:
        """Protocol hook: dashboard calls this during graceful shutdown."""
        self._cancelled = True
        LOGGER.info(
            "stream '%s' stopping after %d events", self.actor_id, self._emitted
        )
        if self._emit_task is None:
            return
        self._emit_task.cancel()
        try:
            await self._emit_task
        except asyncio.CancelledError:
            pass
        finally:
            self._emit_task = None

    async def save_state(self) -> Mapping[str, Any]:
        """Protocol hook: dashboard snapshot for checkpoints."""
        # Persist enough information to replay the same sequence deterministically.
        return {
            "seed": self._seed,
            "emitted": self._emitted,
            "total_events": self._total_events,
            "payload_length": self._payload_length,
        }

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Protocol hook: dashboard restores a checkpoint before resuming."""
        self._seed = int(state.get("seed", self._seed))
        self._rng = random.Random(self._seed)
        self._total_events = int(state.get("total_events", self._total_events))
        self._payload_length = int(state.get("payload_length", self._payload_length))
        self._emitted = int(state.get("emitted", 0))
        # Replay RNG draws so resumed emissions keep the same payload order.
        self._fast_forward_rng(self._emitted)
        if self._emitted >= self._total_events:
            self._emission_complete.set()
        LOGGER.info(
            "stream '%s' restored: emitted=%d total=%d payload_length=%d",
            self.actor_id,
            self._emitted,
            self._total_events,
            self._payload_length,
        )

    async def _emit_events(self) -> None:
        try:
            for _ in range(self._emitted, self._total_events):
                if self._cancelled:
                    break
                payload = self._generate_payload()  # Keeps RNG deterministic per trial.
                preview = _preview_payload(payload)
                LOGGER.info(
                    "stream '%s' emitted event %d/%d payload=%s",
                    self.actor_id,
                    self._emitted,
                    self._total_events,
                    preview,
                )
                await self._publish(
                    StreamEvent(
                        stream_id=self.actor_id,
                        payload=payload,
                        sequence=self._emitted,
                    )
                )
                self._emitted += 1
                if self._interval_seconds:
                    await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            raise
        finally:
            if self._cancelled:
                LOGGER.info(
                    "stream '%s' cancelled after %d events",
                    self.actor_id,
                    self._emitted,
                )
            else:
                LOGGER.info(
                    "stream '%s' completed after %d events",
                    self.actor_id,
                    self._emitted,
                )
            self._emission_complete.set()

    def _generate_payload(self) -> str:
        return "".join(self._rng.choices(Alphabet, k=self._payload_length))

    def _fast_forward_rng(self, emissions: int) -> None:
        # Advance the RNG without publishing events; used only when resuming.
        for _ in range(emissions):
            self._generate_payload()


class CounterOperator(OperatorBase, Operator[CounterOperatorConfig]):
    """Operator that exposes a ""count"" RPC."""

    def __init__(self, actor_id: str) -> None:
        super().__init__(actor_id)
        self._count = 0
        self._lock = asyncio.Lock()

    @classmethod
    def from_dict(
        cls,
        config: CounterOperatorConfig,
    ) -> "CounterOperator":
        return cls(actor_id=str(config["actor_id"]))

    async def start(self) -> None:
        """Protocol hook: dashboard calls this before traffic is routed."""
        LOGGER.info("operator '%s' starting", self.actor_id)
        return None

    async def stop(self) -> None:
        """Protocol hook: dashboard calls this during shutdown."""
        LOGGER.info("operator '%s' stopping at count=%d", self.actor_id, self._count)
        return None

    async def handle_stream_event(
        self, event: StreamEvent[Any]
    ) -> None:  # pragma: no cover - not used
        """Protocol hook: dashboard forwards stream payloads here when routed."""
        del event

    async def save_state(self) -> Mapping[str, Any]:
        """Protocol hook: dashboard snapshot for checkpoints."""
        async with self._lock:
            return {"count": self._count}

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Protocol hook: dashboard restores operator state on resume."""
        async with self._lock:
            self._count = int(state.get("count", 0))
            LOGGER.info(
                "operator '%s' restored to count=%d", self.actor_id, self._count
            )

    async def count(self) -> int:
        async with self._lock:  # RPC invoked by the agent per event.
            self._count += 1
            LOGGER.info(
                "operator '%s' incremented count to %d", self.actor_id, self._count
            )
            return self._count

    @property
    def value(self) -> int:
        return self._count


class CounterAgent(AgentBase, Agent[CounterAgentConfig]):
    """Agent that increments the shared operator counter per event."""

    def __init__(self, actor_id: str, operator_id: str) -> None:
        super().__init__(actor_id)
        self._operator_id = operator_id
        self._operator: _CounterOperatorLike | None = None
        self._events = 0
        self._observed_counts: list[int] = []

    @classmethod
    def from_dict(
        cls,
        config: CounterAgentConfig,
    ) -> "CounterAgent":
        return cls(
            actor_id=str(config["actor_id"]),
            operator_id=str(config["operator_id"]),
        )

    def register_operators(self, operators: Sequence[Operator]) -> None:
        super().register_operators(operators)
        if not operators:
            raise RuntimeError("CounterAgent requires at least one operator")
        operator = operators[0]
        if operator.actor_id != self._operator_id:
            raise RuntimeError(
                f"CounterAgent expected operator '{self._operator_id}'"
                f" but received '{operator.actor_id}'"
            )
        if not hasattr(operator, "count"):
            raise TypeError(
                f"operator '{operator.actor_id}' must expose a 'count' coroutine"
            )
        self._operator = cast(_CounterOperatorLike, operator)

    async def start(self) -> None:
        """Protocol hook: dashboard calls this before events are dispatched."""
        LOGGER.info("agent '%s' starting", self.actor_id)
        return None

    async def stop(self) -> None:
        """Protocol hook: dashboard calls this when the trial is stopping."""
        LOGGER.info("agent '%s' stopping after %d events", self.actor_id, self._events)
        return None

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Protocol hook: dashboard forwards each stream event to subscribed agents."""
        preview = _preview_payload(event.payload)
        self._events += 1  # One RPC per event keeps the operator authoritative.
        operator = self._require_operator()
        new_value = await operator.count()
        self._observed_counts.append(new_value)
        LOGGER.info(
            "agent '%s' handled event seq=%s operator_count=%d payload=%s",
            self.actor_id,
            event.sequence,
            new_value,
            preview,
        )

    async def save_state(self) -> Mapping[str, Any]:
        """Protocol hook: dashboard snapshot for checkpoints."""
        return {
            "events": self._events,
            "observed_counts": list(self._observed_counts),
        }

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Protocol hook: dashboard restores agent state before resuming."""
        self._events = int(state.get("events", 0))
        self._observed_counts = [
            int(value) for value in state.get("observed_counts", [])
        ]
        LOGGER.info(
            "agent '%s' restored: events=%d observed_counts=%d",
            self.actor_id,
            self._events,
            len(self._observed_counts),
        )

    @property
    def events_processed(self) -> int:
        return self._events

    def _require_operator(self) -> _CounterOperatorLike:
        if self._operator is None:
            raise RuntimeError(f"agent '{self.actor_id}' has no registered operator")
        return self._operator


def _build_trial_spec(
    trial_id: str,
    params: BoundedRandomTrialParams,
) -> TrialSpec:
    """Return a :class:`TrialSpec` that wires the sample actors together."""
    # Each actor gets a spec that points at its class plus serialized config.
    operator_config: CounterOperatorConfig = {"actor_id": params.operator_id}
    operator_spec = OperatorSpec(
        actor_id=params.operator_id,
        actor_cls=CounterOperator,
        config=operator_config,
        agent_ids=(params.agent_id,),
    )
    agent_config: CounterAgentConfig = {
        "actor_id": params.agent_id,
        "operator_id": params.operator_id,
    }
    agent_spec = AgentSpec(
        actor_id=params.agent_id,
        actor_cls=CounterAgent,
        config=agent_config,
        operator_ids=(params.operator_id,),
    )
    stream_config: BoundedRandomStringDataStreamConfig = {
        "actor_id": params.stream_id,
        "total_events": params.total_events,
        "payload_length": params.payload_length,
        "interval_seconds": params.interval_seconds,
    }
    if params.seed is not None:
        stream_config["seed"] = params.seed
    stream_spec = DataStreamSpec(
        actor_id=params.stream_id,
        actor_cls=BoundedRandomStringDataStream,
        config=stream_config,
        consumer_ids=(params.operator_id, params.agent_id),
    )
    return TrialSpec(
        trial_id=trial_id,
        data_streams=(stream_spec,),
        operators=(operator_spec,),
        agents=(agent_spec,),
        metadata={
            "sample": "bounded-random-string",
            "total_events": params.total_events,
        },
    )


register_trial_builder(
    "samples.bounded-random",
    BoundedRandomTrialParams,
    _build_trial_spec,
    description="Bounded random string stream feeding a counter agent/operator",
    example_params=BoundedRandomTrialParams(
        total_events=5,
        payload_length=6,
        interval_seconds=0.0,
        seed=1234,
    ),
)


__all__ = [
    "BoundedRandomStringDataStream",
    "CounterAgent",
    "CounterOperator",
    "BoundedRandomTrialParams",
    "BoundedRandomStringDataStreamConfig",
    "CounterAgentConfig",
    "CounterOperatorConfig",
]
