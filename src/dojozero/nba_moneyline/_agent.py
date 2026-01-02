"""Agent implementations for NBA moneyline betting."""

import logging
from typing import Any, Mapping, Protocol, Sequence, TypedDict, cast

from dojozero.agents import BettingAgent
from dojozero.agents.config import BettingAgentConfig
from dojozero.core import Agent, AgentBase, StreamEvent
from dojozero.data._models import DataEvent

logger = logging.getLogger(__name__)


class _ActorIdConfig(TypedDict):
    actor_id: str


class DummyAgentConfig(_ActorIdConfig, total=False):
    operator_id: str  # ID of the event counter operator to use


class _EventCounterOperatorLike(Protocol):
    """Protocol for event counter operator - only the RPC surface is required."""

    async def count(self, event_type: str | None = None) -> int: ...


class DummyAgent(AgentBase, Agent[DummyAgentConfig]):
    """Dummy agent that just counts events.

    This agent receives events from DataHub streams and uses a counter operator
    to track the total number of events processed. It does minimal processing,
    primarily just counting events.
    """

    def __init__(self, actor_id: str, operator_id: str | None = None) -> None:
        super().__init__(actor_id)
        self._operator_id = operator_id
        self._operator: _EventCounterOperatorLike | None = None
        self._events_processed = 0

    @classmethod
    def from_dict(
        cls,
        config: DummyAgentConfig,
    ) -> "DummyAgent":
        return cls(
            actor_id=str(config["actor_id"]),
            operator_id=config.get("operator_id"),
        )

    def register_operators(self, operators: Sequence[Any]) -> None:
        """Register operators that the agent can reach."""
        super().register_operators(operators)

        # Find and register the counter operator if available
        if self._operator_id and operators:
            for operator in operators:
                if operator.actor_id == self._operator_id:
                    if not hasattr(operator, "count"):
                        raise TypeError(
                            f"operator '{operator.actor_id}' must expose a 'count' coroutine"
                        )
                    self._operator = cast(_EventCounterOperatorLike, operator)
                    logger.info(
                        "agent '%s' registered operator '%s'",
                        self.actor_id,
                        self._operator_id,
                    )
                    break

    async def start(self) -> None:
        """Protocol hook: dashboard calls this before events are dispatched."""
        logger.info("agent '%s' starting", self.actor_id)

    async def stop(self) -> None:
        """Protocol hook: dashboard calls this when the trial is stopping."""
        logger.info(
            "agent '%s' stopping after %d events",
            self.actor_id,
            self._events_processed,
        )

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Protocol hook: dashboard forwards each stream event to subscribed agents."""
        data_event: DataEvent = event.payload
        self._events_processed += 1

        # Increment shared counter via operator with event type
        operator_count = None
        if self._operator:
            try:
                operator_count = await self._operator.count(
                    event_type=data_event.event_type
                )
            except Exception as e:
                logger.warning(
                    "agent '%s' failed to increment counter: %s",
                    self.actor_id,
                    e,
                )

        logger.info(
            "agent '%s' handled event seq=%s type=%s operator_count=%s",
            self.actor_id,
            event.sequence,
            data_event.event_type,
            operator_count,
        )

    async def save_state(self) -> Mapping[str, Any]:
        """Protocol hook: dashboard snapshot for checkpoints."""
        return {
            "events_processed": self._events_processed,
        }

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Protocol hook: dashboard restores agent state before resuming."""
        self._events_processed = int(state.get("events_processed", 0))
        logger.info(
            "agent '%s' restored: events=%d",
            self.actor_id,
            self._events_processed,
        )

    @property
    def events_processed(self) -> int:
        return self._events_processed


# ============================================================================
# NBABettingAgent - LLM-based agent that inherits from BettingAgent
# ============================================================================


# Config type alias for clarity
NBABettingAgentConfig = BettingAgentConfig


class NBABettingAgent(BettingAgent):
    """LLM-based betting agent for NBA moneyline betting.

    Inherits from BettingAgent. Uses agent_config_path to load config from YAML.
    """

    @classmethod
    def from_dict(cls, config: NBABettingAgentConfig) -> "NBABettingAgent":
        """Create NBABettingAgent from config dict."""
        # Call parent's from_dict and cast the result
        # BettingAgent.from_dict creates an instance that is compatible
        agent = BettingAgent.from_dict(config)
        # Change the class at runtime to NBABettingAgent
        agent.__class__ = cls
        # Cast for type checker
        return cast("NBABettingAgent", agent)
