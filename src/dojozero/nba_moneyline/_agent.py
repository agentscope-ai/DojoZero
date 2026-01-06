"""Agent implementations for NBA moneyline betting."""

import inspect
import logging
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, TypedDict, cast

from agentscope.agent import ReActAgent
from agentscope.formatter import FormatterBase
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import ChatModelBase
from agentscope.tool import Toolkit

from dojozero.agents import (
    load_agent_config,
    LLMConfig,
    create_model,
    create_formatter,
    create_toolkit,
)
from dojozero.core import Agent, AgentBase, Operator, StreamEvent
from dojozero.data._models import DataEvent

logger = logging.getLogger(__name__)


class _ActorIdConfig(TypedDict):
    actor_id: str


class BettingAgentConfig(_ActorIdConfig, total=False):
    """Betting agent configuration.

    Supports two modes:
    1. agent_config_path: Load config from YAML file
    2. Inline config: Use llm field with LLMConfig
    """

    name: str
    sys_prompt: str
    tools: list[str]  # List of tool names to enable
    llm: LLMConfig  # LLM configuration
    agent_config_path: (
        str  # Path to agent YAML config file (alternative to inline config)
    )


class DummyAgentConfig(_ActorIdConfig, total=False):
    operator_id: str  # ID of the event counter operator to use


class BettingAgent(ReActAgent):
    """ReActAgent extended with DojoZero stream event handling.

    Inherits from agentscope's ReActAgent and adds:
    - actor_id for DojoZero identification
    - Operator registration
    - StreamEvent handling
    - State persistence for checkpointing
    """

    def __init__(
        self,
        actor_id: str,
        name: str,
        sys_prompt: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Toolkit | None = None,
    ) -> None:
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            toolkit=toolkit or Toolkit(),
            memory=InMemoryMemory(),
        )
        self._actor_id = actor_id
        self._operator_registry: dict[str, Operator] = {}
        self._event_count = 0
        self._state: list[dict] = []

    @classmethod
    def from_dict(cls, config: BettingAgentConfig) -> "BettingAgent":
        """Create agent from config dict.

        Supports two modes:
        1. agent_config_path: Load config from YAML file
        2. Inline config: Use config dict directly
        """
        actor_id = config["actor_id"]
        agent_config_path = config.get("agent_config_path")

        if agent_config_path:
            # Load from YAML file
            yaml_config = load_agent_config(agent_config_path)
            llm_config = yaml_config["llm"]
            model_type = llm_config.get("model_type", "openai")
            return cls(
                actor_id=actor_id,
                name=yaml_config.get("name", actor_id),
                sys_prompt=yaml_config.get("sys_prompt", ""),
                model=create_model(llm_config),
                formatter=create_formatter(model_type),
            )

        # Inline config mode
        llm_config = config.get("llm", {})
        model_type = llm_config.get("model_type", "openai")
        return cls(
            actor_id=actor_id,
            name=config.get("name", actor_id),
            sys_prompt=config.get("sys_prompt", ""),
            model=create_model(llm_config),
            formatter=create_formatter(model_type),
        )

    @classmethod
    def from_yaml(
        cls,
        config_path: str | Path,
        actor_id: str,
        toolkit: Toolkit | None = None,
    ) -> "BettingAgent":
        """Create agent from YAML config file.

        Args:
            config_path: Path to YAML config file
            actor_id: The actor ID for this agent
            toolkit: Optional toolkit to use
        """
        config = load_agent_config(config_path)
        llm_config = config["llm"]
        model_type = llm_config.get("model_type", "openai")

        return cls(
            actor_id=actor_id,
            name=config["name"],
            sys_prompt=config["sys_prompt"],
            model=create_model(llm_config),
            formatter=create_formatter(model_type),
            toolkit=toolkit or Toolkit(),
        )

    @property
    def actor_id(self) -> str:
        return self._actor_id

    async def register_operators(self, operators: Sequence[Operator]) -> None:
        """Register operators and auto-register broker tools if available."""
        all_tools = []
        for op in operators:
            self._operator_registry[op.actor_id] = op
            logger.info(
                "agent '%s' registered operator '%s'", self.actor_id, op.actor_id
            )
            agent_tools = getattr(op, "agent_tools", None)
            if callable(agent_tools):
                tools_result = agent_tools(self.actor_id, operator=op)
                if inspect.iscoroutine(tools_result):
                    tools_result = await tools_result
                # After awaiting, tools_result should be a list
                if isinstance(tools_result, list):
                    all_tools.extend(tools_result)
                    logger.info(
                        "agent '%s' registered %d tools from '%s'",
                        self.actor_id,
                        len(tools_result),
                        op.actor_id,
                    )

        if all_tools:
            self.toolkit = create_toolkit(all_tools)

    @property
    def operators(self) -> tuple[str, ...]:
        return tuple(self._operator_registry.keys())

    def get_operator(self, operator_id: str) -> Operator:
        """Get a registered operator by ID."""
        return self._operator_registry[operator_id]

    async def start(self) -> None:
        """Start the agent."""
        logger.info("agent '%s' starting", self.actor_id)

    async def stop(self) -> None:
        """Stop the agent."""
        logger.info(
            "agent '%s' stopping after %d events", self.actor_id, self._event_count
        )

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process incoming stream event with the ReActAgent.

        All LLM interactions and agent behaviors are automatically traced by AgentScope's
        native tracing system. No manual recording is needed - traces are captured via
        @trace_llm and @trace_reply decorators in the base classes.
        """
        self._event_count += 1
        logger.info(
            "agent '%s' received event seq=%s from stream '%s'",
            self.actor_id,
            event.sequence,
            event.stream_id,
        )

        input_content = f"New data: {event.payload}"
        msg = Msg(name="event_push", content=input_content, role="user")

        # Call agent - all interactions are automatically traced by AgentScope
        await self(msg)

        memory = await self.memory.get_memory()
        memory = [msg.to_dict() for msg in memory]
        self._state.append({event.stream_id: memory})

        logger.info(
            "agent '%s' processed event seq=%s",
            self.actor_id,
            event.sequence,
        )

    async def save_state(self) -> Mapping[str, Any]:
        """Return serializable state for checkpointing."""
        return {"events": self._event_count, "state": self._state}

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Restore state from checkpoint.

        Note: LLM interactions are retrieved from AgentScope's trace store, not from state.
        """
        self._event_count = int(state.get("events", 0))
        self._state = state.get("state", [])
        logger.info(
            "agent '%s' restored: events_processed=%d",
            self.actor_id,
            self._event_count,
        )

    @property
    def event_count(self) -> int:
        return self._event_count

    def register_toolkit(self, toolkit: Toolkit) -> None:
        """Register toolkit for Ray actor compatibility."""
        self.toolkit = toolkit


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
