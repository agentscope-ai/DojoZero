"""Agent implementations for NBA moneyline betting."""

import asyncio
import inspect
import json
import logging
from collections import deque
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
from dojozero.core._tracing import create_span_from_event, emit_span
from dojozero.data._models import DataEvent
from dojozero.nba_moneyline._formatters import format_event, parse_response_content

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
    - StreamEvent handling with event queueing
    - State persistence for checkpointing
    - OTLP trace streaming for agent messages

    Event Handling:
    - Events are formatted to LLM-friendly text based on their type
    - When the agent is busy processing, new events are queued
    - After processing, queued events are consolidated into a single message
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
        self._trial_id: str | None = None
        self._operator_registry: dict[str, Operator] = {}
        self._event_count = 0
        self._state: list[dict] = []
        # Event queue for handling concurrent events
        self._event_queue: deque[StreamEvent[Any]] = deque()
        self._is_processing: bool = False
        self._processing_lock = asyncio.Lock()

    @property
    def trial_id(self) -> str | None:
        """Trial ID this agent belongs to (set by Dashboard)."""
        return self._trial_id

    def set_trial_id(self, trial_id: str) -> None:
        """Set the trial ID for this agent (called by Dashboard)."""
        self._trial_id = trial_id

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

    def _emit_agent_span(
        self,
        stream_id: str,
        operation_name: str,
        role: str,
        content: str,
        tool_calls: list[dict] | None = None,
    ) -> None:
        """Emit a span for an agent message to the OTel exporter."""
        if self._trial_id is None:
            return

        tags: dict[str, Any] = {
            "dojozero.event.type": operation_name,
            "dojozero.event.sequence": self._event_count,
            "event.stream_id": stream_id,
            "event.role": role,
            "event.name": self.name,
            "event.content": content,
        }

        if tool_calls:
            tags["event.tool_calls"] = json.dumps(tool_calls, default=str)

        span = create_span_from_event(
            trial_id=self._trial_id,
            actor_id=self._actor_id,
            operation_name=operation_name,
            extra_tags=tags,
        )
        emit_span(span)

    def _format_events_for_llm(self, events: list[StreamEvent[Any]]) -> str:
        """Format multiple events into a consolidated LLM-friendly message.

        Args:
            events: List of StreamEvent objects to format

        Returns:
            Consolidated human-readable message for the LLM
        """
        if len(events) == 1:
            # Single event: format directly
            event = events[0]
            payload = event.payload
            if isinstance(payload, DataEvent):
                return format_event(payload)
            else:
                return f"[New data]: {json.dumps(payload, default=str, ensure_ascii=False)}"

        # Multiple events: consolidate with headers
        lines = [f"[{len(events)} New Events Received]\n"]

        for i, event in enumerate(events, 1):
            payload = event.payload
            if isinstance(payload, DataEvent):
                formatted = format_event(payload)
            else:
                formatted = (
                    f"[Data]: {json.dumps(payload, default=str, ensure_ascii=False)}"
                )

            lines.append(f"--- Event {i} (from {event.stream_id}) ---")
            lines.append(formatted)
            lines.append("")

        return "\n".join(lines)

    async def _process_events(self, events: list[StreamEvent[Any]]) -> None:
        """Process a batch of events.

        Args:
            events: List of events to process together
        """
        if not events:
            return

        # Update event count
        self._event_count += len(events)

        # Format events for LLM
        input_content = self._format_events_for_llm(events)

        # Log event processing
        stream_ids = [e.stream_id for e in events]
        logger.info(
            "agent '%s' processing %d event(s) from streams: %s",
            self.actor_id,
            len(events),
            stream_ids,
        )

        msg = Msg(name="event_push", content=input_content, role="user")

        # Emit span for user input (use first event's stream_id for tracing)
        primary_stream_id = events[0].stream_id
        self._emit_agent_span(
            stream_id=primary_stream_id,
            operation_name="agent.input",
            role="user",
            content=input_content,
        )

        # Call agent
        response = await self(msg)

        # Emit span for agent response
        if response is not None:
            content = getattr(response, "content", None)
            text_content, tool_calls = parse_response_content(content)
            self._emit_agent_span(
                stream_id=primary_stream_id,
                operation_name="agent.response",
                role="assistant",
                content=text_content,
                tool_calls=tool_calls,
            )

        # Update state
        memory = await self.memory.get_memory()
        memory_dicts = [m.to_dict() for m in memory]
        for event in events:
            self._state.append({event.stream_id: memory_dicts})

        logger.info(
            "agent '%s' processed %d event(s)",
            self.actor_id,
            len(events),
        )

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process incoming stream event with the ReActAgent.

        Event handling behavior:
        - Events are formatted to LLM-friendly text based on their type
        - If the agent is busy processing, new events are queued
        - After processing completes, all queued events are consolidated
          into a single message for the next processing cycle

        Emits OTLP trace spans for each agent message:
        - agent.input: User input from stream event(s)
        - agent.response: Assistant response from LLM
        """
        logger.info(
            "agent '%s' received event seq=%s from stream '%s'",
            self.actor_id,
            event.sequence,
            event.stream_id,
        )

        async with self._processing_lock:
            if self._is_processing:
                # Agent is busy, queue this event
                self._event_queue.append(event)
                logger.info(
                    "agent '%s' queued event seq=%s (queue size: %d)",
                    self.actor_id,
                    event.sequence,
                    len(self._event_queue),
                )
                return

            # Mark as processing
            self._is_processing = True

        try:
            # Process the current event
            await self._process_events([event])

            # Process any queued events
            while True:
                async with self._processing_lock:
                    if not self._event_queue:
                        # No more queued events
                        self._is_processing = False
                        break

                    # Collect all queued events
                    queued_events = list(self._event_queue)
                    self._event_queue.clear()

                logger.info(
                    "agent '%s' processing %d queued event(s)",
                    self.actor_id,
                    len(queued_events),
                )
                await self._process_events(queued_events)

        except Exception:
            # Ensure we reset the processing flag on error
            async with self._processing_lock:
                self._is_processing = False
            raise

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
    primarily just counting events. Emits OTLP trace spans for processed events.
    """

    def __init__(self, actor_id: str, operator_id: str | None = None) -> None:
        super().__init__(actor_id)
        self._operator_id = operator_id
        self._operator: _EventCounterOperatorLike | None = None
        self._events_processed = 0

    def _emit_event_span(
        self,
        stream_id: str,
        event_type: str,
        operator_count: int | None,
    ) -> None:
        """Emit a span for a processed event to the OTel exporter."""
        if self.trial_id is None:
            return

        tags: dict[str, Any] = {
            "dojozero.event.type": "agent.event_processed",
            "dojozero.event.sequence": self._events_processed,
            "event.stream_id": stream_id,
            "event.original_event_type": event_type,
        }
        if operator_count is not None:
            tags["event.operator_count"] = operator_count

        span = create_span_from_event(
            trial_id=self.trial_id,
            actor_id=self.actor_id,
            operation_name="agent.event_processed",
            extra_tags=tags,
        )
        emit_span(span)

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

        # Emit trace span for this event
        self._emit_event_span(
            stream_id=event.stream_id,
            event_type=data_event.event_type,
            operator_count=operator_count,
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
