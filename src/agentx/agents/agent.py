"""Agent implementation extending AgentScope's ReActAgent for AgentX."""

import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

from agentscope.agent import ReActAgent
from agentscope.formatter import FormatterBase
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import ChatModelBase
from agentscope.tool import Toolkit

from agentx.core import Operator, StreamEvent

from .config import load_agent_config, BettingAgentConfig
from .model import _create_model_from_llm_config, _create_model, _create_formatter

LOGGER = logging.getLogger("agentx.agents")


class BettingAgent(ReActAgent):
    """ReActAgent extended with AgentX stream event handling.

    Inherits from agentscope's ReActAgent and adds:
    - actor_id for AgentX identification
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
        actor_id = config.get("actor_id", "")
        agent_config_path = config.get("agent_config_path")

        if agent_config_path:
            # Load from YAML file
            yaml_config = load_agent_config(agent_config_path)
            llm_config = yaml_config["llm"]
            model_type = llm_config.get("model_type", "openai")
            return cls(
                actor_id=actor_id or yaml_config["agent_id"],
                name=yaml_config.get("name", actor_id),
                sys_prompt=yaml_config.get("sys_prompt", ""),
                model=_create_model_from_llm_config(llm_config),
                formatter=_create_formatter(model_type),
                # model_name=model_name,
            )

        # Inline config mode
        model_type = config.get("model_type", "openai")
        return cls(
            actor_id=actor_id,
            name=config.get("name", actor_id),
            sys_prompt=config.get("sys_prompt", ""),
            model=_create_model(config),
            formatter=_create_formatter(model_type),
        )

    @classmethod
    def from_yaml(
        cls,
        config_path: str | Path,
        toolkit: Toolkit | None = None,
    ) -> "BettingAgent":
        """Create agent from YAML config file."""
        config = load_agent_config(config_path)
        llm_config = config["llm"]
        model_type = llm_config.get("model_type", "openai")

        return cls(
            actor_id=config["agent_id"],
            name=config["name"],
            sys_prompt=config["sys_prompt"],
            model=_create_model_from_llm_config(llm_config),
            formatter=_create_formatter(model_type),
            toolkit=toolkit or Toolkit(),
        )

    @property
    def actor_id(self) -> str:
        return self._actor_id

    async def register_operators(self, operators: Sequence[Operator]) -> None:
        """Register operators and auto-register broker tools if available."""
        import inspect
        from .toolkit import create_toolkit

        all_tools = []
        for op in operators:
            self._operator_registry[op.actor_id] = op
            LOGGER.info(
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
                    LOGGER.info(
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
        LOGGER.info("agent '%s' starting", self.actor_id)

    async def stop(self) -> None:
        """Stop the agent."""
        LOGGER.info(
            "agent '%s' stopping after %d events", self.actor_id, self._event_count
        )

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process incoming stream event with the ReActAgent.

        All LLM interactions and agent behaviors are automatically traced by AgentScope's
        native tracing system. No manual recording is needed - traces are captured via
        @trace_llm and @trace_reply decorators in the base classes.
        """
        self._event_count += 1
        LOGGER.info(
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

        LOGGER.info(
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
        LOGGER.info(
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
