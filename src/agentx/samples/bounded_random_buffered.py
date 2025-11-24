"""Buffered variant of the bounded random reference environment.

This module reuses the bounded random stream and operator actors but swaps in a
buffered agent that batches incoming events. The buffer drains on a fixed
interval so the operator's counter increments in bursts rather than per-event.
"""

import asyncio
import logging
from typing import Any, Mapping, Protocol, cast

from pydantic import Field

from agentx.core import (
    ActorRuntimeContext,
    ActorSpec,
    Agent,
    AgentBase,
    DataStreamSpec,
    Operator,
    register_trial_builder,
    StreamEvent,
    TrialSpec,
)

from .bounded_random import (
    BoundedRandomStringDataStream,
    BoundedRandomStringDataStreamConfig,
    BoundedRandomTrialConfig,
    CounterAgentConfig,
    CounterOperator,
    CounterOperatorConfig,
)

LOGGER = logging.getLogger("agentx.samples.bounded_random_buffered")


def _preview_payload(value: Any, *, limit: int = 32) -> str:
    text = value if isinstance(value, str) else repr(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


class CounterAgentBufferedConfig(CounterAgentConfig, total=False):
    flush_interval_seconds: float


class BoundedRandomBufferedTrialConfig(BoundedRandomTrialConfig):
    buffer_flush_seconds: float = Field(default=5.0, gt=0.0)
    agent_id: str = "counter-agent-buffered"


class _CounterOperatorLike(Operator[CounterOperatorConfig], Protocol):
    async def count(self) -> int: ...


class CounterAgentBuffered(AgentBase, Agent[CounterAgentBufferedConfig]):
    """Agent that buffers events and processes them on a timed cadence."""

    def __init__(
        self,
        actor_id: str,
        operator: _CounterOperatorLike,
        flush_interval_seconds: float,
    ) -> None:
        super().__init__(actor_id, operators=(operator,))
        self._operator = operator
        self._flush_interval = max(float(flush_interval_seconds), 0.001)
        self._events = 0
        self._observed_counts: list[int] = []
        self._buffer: list[dict[str, Any]] = []
        self._buffer_lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None

    @classmethod
    def from_dict(
        cls,
        config: CounterAgentBufferedConfig,
        *,
        context: ActorRuntimeContext | None = None,
    ) -> "CounterAgentBuffered":
        if context is None:
            raise RuntimeError("CounterAgentBuffered requires runtime context")
        operator_id = str(config["operator_id"])
        operator_ref = context.operators.get(operator_id)
        if operator_ref is None:
            raise RuntimeError(
                f"CounterAgentBuffered could not find operator '{operator_id}' in context"
            )
        if not hasattr(operator_ref, "count"):
            raise TypeError(f"operator '{operator_id}' must expose a 'count' coroutine")
        interval = float(config.get("flush_interval_seconds", 5.0))
        operator_like = cast(_CounterOperatorLike, operator_ref)
        return cls(
            actor_id=str(config["actor_id"]),
            operator=operator_like,
            flush_interval_seconds=interval,
        )

    async def start(self) -> None:
        LOGGER.info(
            "buffered agent '%s' starting with flush_interval=%ss",
            self.actor_id,
            self._flush_interval,
        )
        if self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        LOGGER.info(
            "buffered agent '%s' stopping after %d processed events",
            self.actor_id,
            self._events,
        )
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            finally:
                self._flush_task = None
        async with self._buffer_lock:
            pending = len(self._buffer)
        if pending:
            LOGGER.info(
                "buffered agent '%s' leaving %d events buffered for persistence",
                self.actor_id,
                pending,
            )

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        preview = _preview_payload(event.payload)
        async with self._buffer_lock:
            self._buffer.append({"sequence": event.sequence, "payload": event.payload})
            pending = len(self._buffer)
        LOGGER.info(
            "buffered agent '%s' queued event seq=%s buffer_size=%d payload=%s",
            self.actor_id,
            event.sequence,
            pending,
            preview,
        )

    async def save_state(self) -> Mapping[str, Any]:
        async with self._buffer_lock:
            snapshot = [dict(item) for item in self._buffer]
        return {
            "events": self._events,
            "observed_counts": list(self._observed_counts),
            "buffer": snapshot,
        }

    async def load_state(self, state: Mapping[str, Any]) -> None:
        self._events = int(state.get("events", 0))
        self._observed_counts = [
            int(value) for value in state.get("observed_counts", [])
        ]
        buffer_items = [
            {
                "sequence": item.get("sequence"),
                "payload": item.get("payload"),
            }
            for item in state.get("buffer", [])
        ]
        async with self._buffer_lock:
            self._buffer = buffer_items
        LOGGER.info(
            "buffered agent '%s' restored: events=%d buffered=%d observed_counts=%d",
            self.actor_id,
            self._events,
            len(buffer_items),
            len(self._observed_counts),
        )

    async def _flush_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._flush_interval)
                await self._flush_buffer(reason="interval")
        except asyncio.CancelledError:
            raise

    async def _flush_buffer(self, *, reason: str) -> None:
        async with self._buffer_lock:
            if not self._buffer or reason == "shutdown":
                return
            batch = tuple(self._buffer)
            self._buffer.clear()
        LOGGER.info(
            "buffered agent '%s' flushing %d buffered events due to %s",
            self.actor_id,
            len(batch),
            reason,
        )
        for record in batch:
            preview = _preview_payload(record.get("payload"))
            self._events += 1
            new_value = await self._operator.count()
            self._observed_counts.append(new_value)
            LOGGER.info(
                "buffered agent '%s' processed buffered seq=%s operator_count=%d reason=%s payload=%s",
                self.actor_id,
                record.get("sequence"),
                new_value,
                reason,
                preview,
            )


def _build_buffered_trial_spec(
    trial_id: str,
    config: BoundedRandomBufferedTrialConfig,
) -> TrialSpec:
    operator_config: CounterOperatorConfig = {"actor_id": config.operator_id}
    operator_spec = ActorSpec(
        actor_id=config.operator_id,
        actor_cls=CounterOperator,
        config=operator_config,
    )
    agent_config: CounterAgentBufferedConfig = {
        "actor_id": config.agent_id,
        "operator_id": config.operator_id,
        "flush_interval_seconds": config.buffer_flush_seconds,
    }
    agent_spec = ActorSpec(
        actor_id=config.agent_id,
        actor_cls=CounterAgentBuffered,
        config=agent_config,
    )
    stream_config: BoundedRandomStringDataStreamConfig = {
        "actor_id": config.stream_id,
        "total_events": config.total_events,
        "payload_length": config.payload_length,
        "interval_seconds": config.interval_seconds,
    }
    if config.seed is not None:
        stream_config["seed"] = config.seed
    stream_spec = DataStreamSpec(
        actor_id=config.stream_id,
        actor_cls=BoundedRandomStringDataStream,
        config=stream_config,
        consumers=(config.operator_id, config.agent_id),
    )
    return TrialSpec(
        trial_id=trial_id,
        data_streams=(stream_spec,),
        operators=(operator_spec,),
        agents=(agent_spec,),
        metadata={
            "sample": "bounded-random-buffered",
            "total_events": config.total_events,
            "buffer_flush_seconds": config.buffer_flush_seconds,
        },
    )


register_trial_builder(
    "samples.bounded-random-buffered",
    BoundedRandomBufferedTrialConfig,
    _build_buffered_trial_spec,
    description=(
        "Bounded random stream with buffered agent that flushes every few seconds"
    ),
    example_config=BoundedRandomBufferedTrialConfig(
        total_events=5,
        payload_length=6,
        interval_seconds=0.0,
        seed=1234,
        buffer_flush_seconds=5.0,
    ),
)


__all__ = [
    "BoundedRandomBufferedTrialConfig",
    "CounterAgentBuffered",
    "CounterAgentBufferedConfig",
]
