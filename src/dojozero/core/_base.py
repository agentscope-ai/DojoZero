"""Reusable base classes for concrete DojoZero actor implementations."""

import asyncio
import logging
from abc import ABC
from typing import Any, Dict, Sequence

from ._actors import Agent, Operator
from ._types import StreamEvent

LOGGER = logging.getLogger("dojozero.base")


class ActorBase(ABC):
    """Base class for all DojoZero actors that enforces common requirements."""

    def __init__(self, actor_id: str, trial_id: str, *, sport_type: str = "") -> None:
        self._actor_id = actor_id
        self._trial_id = trial_id
        self._sport_type = sport_type

    @property
    def actor_id(self) -> str:
        return self._actor_id

    @property
    def trial_id(self) -> str:
        """Trial ID this actor belongs to."""
        return self._trial_id

    @property
    def sport_type(self) -> str:
        """Sport type for this actor's trial (e.g., 'nba', 'nfl')."""
        return self._sport_type


class AgentBase(ActorBase, ABC):
    """Base helper for agents to expose and access reachable operators."""

    def __init__(self, actor_id: str, trial_id: str, *, sport_type: str = "") -> None:
        super().__init__(actor_id, trial_id, sport_type=sport_type)
        self._operator_registry: Dict[str, Operator] = {}

    def register_operators(self, operators: Sequence[Operator]) -> None:
        """Register operators that the agent can reach."""
        for operator in operators:
            if operator.actor_id in self._operator_registry:
                raise ValueError(
                    f"Operator with ID {operator.actor_id} is already registered."
                )
            self._operator_registry[operator.actor_id] = operator

    @property
    def operators(self) -> tuple[str, ...]:
        return tuple(self._operator_registry.keys())


class DataStreamBase(ActorBase, ABC):
    """Base helper for stream actors that manages consumer fan-out."""

    def __init__(self, actor_id: str, trial_id: str, *, sport_type: str = "") -> None:
        super().__init__(actor_id, trial_id, sport_type=sport_type)
        self._consumer_registry: Dict[str, "Agent | Operator"] = {}

    def register_consumers(self, consumers: Sequence["Agent | Operator"]) -> None:
        """Register consumers to receive stream events."""
        for consumer in consumers:
            if consumer.actor_id in self._consumer_registry:
                raise ValueError(
                    f"Consumer with ID {consumer.actor_id} is already registered."
                )
            self._consumer_registry[consumer.actor_id] = consumer

    @property
    def consumers(self) -> tuple[str, ...]:
        return tuple(self._consumer_registry.keys())

    async def _publish(self, event: StreamEvent[Any]) -> None:
        """Publish a stream event to all registered consumers.

        Note: Span emission is handled by DataHub._emit_event_span() when the
        event is received from the store. We don't emit here to avoid duplicates.
        """
        if not self._consumer_registry:
            return

        await asyncio.gather(
            *(
                consumer.handle_stream_event(event)
                for consumer in self._consumer_registry.values()
            )
        )


class OperatorBase(ActorBase, ABC):
    """Base helper for operator actors to handle stream events."""

    def __init__(self, actor_id: str, trial_id: str, *, sport_type: str = "") -> None:
        super().__init__(actor_id, trial_id, sport_type=sport_type)
        self._agent_registry: Dict[str, Agent] = {}

    def register_agents(self, agents: Sequence[Agent]) -> None:
        """Register agents that can be notified of stream events."""
        for agent in agents:
            if agent.actor_id in self._agent_registry:
                raise ValueError(
                    f"Agent with ID {agent.actor_id} is already registered."
                )
            self._agent_registry[agent.actor_id] = agent

    @property
    def agents(self) -> tuple[str, ...]:
        return tuple(self._agent_registry.keys())

    async def _notify_agent(self, agent_id: str, event: StreamEvent[Any]) -> None:
        """Notify a specific agent about a stream event."""
        if not self._agent_registry:
            return
        agent = self._agent_registry.get(agent_id)
        if agent:
            await agent.handle_stream_event(event)
