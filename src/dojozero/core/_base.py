"""Reusable base classes for concrete DojoZero actor implementations."""

import asyncio
import json
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

        Also emits a span to the global OTel exporter if configured.
        """
        if not self._consumer_registry:
            return

        # Emit span for event publication
        self._emit_event_span(event)

        await asyncio.gather(
            *(
                consumer.handle_stream_event(event)
                for consumer in self._consumer_registry.values()
            )
        )

    def _emit_event_span(self, event: StreamEvent[Any]) -> None:
        """Emit a span for a stream event to the OTel exporter."""
        from ._tracing import emit_span, create_span_from_event

        # Determine event type from payload if available
        event_type = "stream.event"
        payload = event.payload
        if hasattr(payload, "event_type"):
            event_type = payload.event_type
        elif isinstance(payload, dict) and "event_type" in payload:
            event_type = payload["event_type"]

        # Build tags for the span
        tags: dict[str, Any] = {
            "dojozero.event.type": event_type,
            "dojozero.event.sequence": event.sequence,
            "dojozero.sport.type": self._sport_type,
        }

        # Add payload data as event.* tags
        if isinstance(payload, dict):
            payload_dict = payload
        elif hasattr(payload, "to_dict") and callable(getattr(payload, "to_dict")):
            payload_dict = getattr(payload, "to_dict")()
        else:
            payload_dict = {"data": str(payload)}

        for key, value in payload_dict.items():
            if key in ("event_type", "timestamp", "actor_id", "stream_id"):
                continue  # Skip metadata fields
            if isinstance(value, (dict, list)):
                tags[f"event.{key}"] = json.dumps(value, default=str)
            else:
                tags[f"event.{key}"] = value

        # Extract game_id as top-level tag for easier querying
        game_id = payload_dict.get("game_id") or payload_dict.get("event_id", "")
        if game_id:
            # Handle event_id format like "0022400608_pbp_188" -> extract game_id
            if "_" in str(game_id) and str(game_id).startswith("00"):
                game_id = str(game_id).split("_")[0]
            tags["dojozero.game.id"] = str(game_id)

        span = create_span_from_event(
            trial_id=self._trial_id,
            actor_id=self._actor_id,
            operation_name=event_type,
            start_time=event.emitted_at,
            extra_tags=tags,
        )
        emit_span(span)


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
