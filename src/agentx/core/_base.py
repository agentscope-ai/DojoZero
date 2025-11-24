"""Reusable base classes for concrete AgentX actor implementations."""

import asyncio
from abc import ABC
from typing import Any, Dict, Sequence

from ._actors import Agent, Operator
from ._types import StreamEvent


class ActorBase(ABC):
    """Base class for all AgentX actors that enforces common requirements."""

    def __init__(self, actor_id: str) -> None:
        self._actor_id = actor_id

    @property
    def actor_id(self) -> str:
        return self._actor_id


class AgentBase(ActorBase, ABC):
    """Base helper for agents to expose and access reachable operators."""

    def __init__(
        self, actor_id: str, *, operators: Sequence[Operator] | None = None
    ) -> None:
        super().__init__(actor_id)
        self._operator_registry: Dict[str, Operator] = {
            operator.actor_id: operator for operator in operators or ()
        }

    @property
    def operators(self) -> tuple[str, ...]:
        return tuple(self._operator_registry.keys())


class DataStreamBase(ActorBase, ABC):
    """Base helper for stream actors that manages consumer fan-out."""

    def __init__(
        self, actor_id: str, *, consumers: Sequence["Agent | Operator"] | None = None
    ) -> None:
        super().__init__(actor_id)
        self._consumer_registry: Dict[str, "Agent | Operator"] = {
            consumer.actor_id: consumer for consumer in consumers or ()
        }

    @property
    def consumers(self) -> tuple[str, ...]:
        return tuple(self._consumer_registry.keys())

    async def _publish(self, event: StreamEvent[Any]) -> None:
        if not self._consumer_registry:
            return
        await asyncio.gather(
            *(
                consumer.handle_stream_event(event)
                for consumer in self._consumer_registry.values()
            )
        )
