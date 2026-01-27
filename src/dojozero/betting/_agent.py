"""Generic betting agent implementation.

This module provides a BettingAgent class that uses ReActAgent for LLM reasoning
in sports betting scenarios. It's sport-agnostic and can be used with any
betting scenario (NBA, NFL, etc.) by providing appropriate formatters.
"""

import asyncio
import inspect
import json
import logging
from collections import Counter, deque
from typing import Any, Callable, Mapping, Sequence, TypedDict

from agentscope.agent import ReActAgent
from agentscope.formatter import FormatterBase
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import ChatModelBase
from agentscope.tool import Toolkit

from dojozero.agents import (
    LLMConfig,
    create_model,
    create_formatter,
    create_toolkit,
)
from dojozero.core import RuntimeContext, Agent, AgentBase, Operator, StreamEvent
from dojozero.core._tracing import create_span_from_event, emit_span
from dojozero.data._models import DataEvent, extract_game_id

logger = logging.getLogger(__name__)


# Type for event formatter function
EventFormatter = Callable[[DataEvent], str]


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


def _default_format_event(event: DataEvent) -> str:
    """Default event formatter - converts to JSON."""
    event_type = getattr(event, "event_type", "unknown")
    event_dict = event.to_dict() if hasattr(event, "to_dict") else str(event)
    return f"[{event_type}]: {json.dumps(event_dict, default=str, ensure_ascii=False)}"


def _parse_response_content(content: Any) -> tuple[str, list[dict] | None]:
    """Parse LLM response content into text and tool calls.

    Handles content as a list of dicts with types:
    - "text": Contains text content
    - "tool_use": Contains tool call info
    - "tool_result": Contains tool result info

    Args:
        content: Response content (can be list[dict], str, or None)

    Returns:
        Tuple of (text_content, tool_calls)
    """
    if content is None:
        return "", None

    if not isinstance(content, list):
        return str(content), None

    text_parts = []
    tool_calls = []

    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            text_parts.append(item.get("text", ""))
        elif item_type in ("tool_use", "tool_result"):
            tool_calls.append(item)

    return "".join(text_parts), tool_calls or None


class BettingAgent(AgentBase, Agent[BettingAgentConfig]):
    """Generic agent for sports betting that uses ReActAgent for LLM reasoning.

    Inherits from AgentBase to implement the Actor protocol and uses
    composition to wrap an agentscope ReActAgent for LLM functionality.

    Features:
    - actor_id and trial_id for DojoZero identification
    - Operator registration
    - StreamEvent handling with event queueing
    - State persistence for checkpointing
    - OTLP trace streaming for agent messages
    - Pluggable event formatters for sport-specific formatting

    Event Handling:
    - Events are formatted to LLM-friendly text based on their type
    - When the agent is busy processing, new events are queued
    - After processing, queued events are consolidated into a single message
    """

    def __init__(
        self,
        actor_id: str,
        trial_id: str,
        name: str,
        sys_prompt: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Toolkit | None = None,
        event_formatter: EventFormatter | None = None,
    ) -> None:
        super().__init__(actor_id, trial_id)
        # Create internal ReActAgent for LLM reasoning
        self._react_agent = ReActAgent(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            toolkit=toolkit or Toolkit(),
            memory=InMemoryMemory(),
        )
        self._event_count = 0
        self._state: list[dict] = []
        # Event queue stores (event, retry_count) tuples for retry tracking
        self._event_queue: deque[tuple[StreamEvent[Any], int]] = deque()
        self._is_processing: bool = False
        self._processing_lock = asyncio.Lock()
        self._max_event_retry_count = 3
        # Event formatter for converting DataEvents to LLM-friendly text
        self._event_formatter = event_formatter or _default_format_event

    @property
    def name(self) -> str:
        """Agent name from the internal ReActAgent."""
        return self._react_agent.name

    @property
    def memory(self) -> Any:
        """Memory from the internal ReActAgent."""
        return self._react_agent.memory

    @property
    def toolkit(self) -> Toolkit:
        """Toolkit from the internal ReActAgent."""
        return self._react_agent.toolkit

    @toolkit.setter
    def toolkit(self, value: Toolkit) -> None:
        """Set toolkit on the internal ReActAgent."""
        self._react_agent.toolkit = value

    def set_event_formatter(self, formatter: EventFormatter) -> None:
        """Set a custom event formatter for this agent.

        Args:
            formatter: Function that takes a DataEvent and returns a string
        """
        self._event_formatter = formatter

    @classmethod
    def from_dict(
        cls,
        config: BettingAgentConfig,
        context: RuntimeContext,
    ) -> "BettingAgent":
        """Create agent from config dict.

        Note: agent_config_path is no longer supported here - the trial builder
        handles YAML loading and expansion. This method expects inline configs
        with a single LLMConfig.
        """
        actor_id = config["actor_id"]

        # Inline config mode (already expanded by trial builder)
        llm_config = config.get("llm", {})
        model_type = llm_config.get("model_type", "openai")
        return cls(
            actor_id=actor_id,
            trial_id=context.trial_id,
            name=config.get("name", actor_id),
            sys_prompt=config.get("sys_prompt", ""),
            model=create_model(llm_config),
            formatter=create_formatter(model_type),
        )

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

    def _extract_game_id_from_events(self, events: list[StreamEvent[Any]]) -> str:
        """Extract game_id from event payloads.

        Args:
            events: List of StreamEvent objects

        Returns:
            game_id string, or empty string if not found
        """
        for event in events:
            payload = event.payload
            if isinstance(payload, DataEvent):
                game_id = extract_game_id(payload.to_dict())
                if game_id:
                    return game_id
        return ""

    def _emit_agent_span(
        self,
        stream_id: str,
        operation_name: str,
        role: str,
        content: str,
        tool_calls: list[dict] | None = None,
        game_id: str = "",
    ) -> None:
        """Emit a span for an agent message to the OTel exporter."""
        tags: dict[str, Any] = {
            "sequence": self._event_count,
            "event.stream_id": stream_id,
            "event.role": role,
            "event.name": self.name,
            "event.content": content,
        }

        if tool_calls:
            tags["event.tool_calls"] = json.dumps(tool_calls, default=str)

        if game_id:
            tags["game.id"] = game_id

        span = create_span_from_event(
            trial_id=self.trial_id,
            actor_id=self.actor_id,
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
                return self._event_formatter(payload)
            else:
                return f"[New data]: {json.dumps(payload, default=str, ensure_ascii=False)}"

        # Multiple events: consolidate with headers
        lines = [f"[{len(events)} New Events Received]\n"]

        for i, event in enumerate(events, 1):
            payload = event.payload
            if isinstance(payload, DataEvent):
                formatted = self._event_formatter(payload)
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

        # Log event processing with stream count summary
        stream_counts = Counter(e.stream_id for e in events)
        stream_summary = ", ".join(f"{k}:{v}" for k, v in stream_counts.most_common())
        logger.info(
            "agent '%s' processing %d event(s) from streams: {%s}",
            self.actor_id,
            len(events),
            stream_summary,
        )

        msg = Msg(name="event_push", content=input_content, role="user")

        # Emit span for user input (use first event's stream_id for tracing)
        primary_stream_id = events[0].stream_id
        game_id = self._extract_game_id_from_events(events)
        self._emit_agent_span(
            stream_id=primary_stream_id,
            operation_name="agent.input",
            role="user",
            content=input_content,
            game_id=game_id,
        )

        # Call agent
        response = await self._react_agent(msg)

        # Emit span for agent response
        if response is not None:
            content = getattr(response, "content", None)
            text_content, tool_calls = _parse_response_content(content)
            self._emit_agent_span(
                stream_id=primary_stream_id,
                operation_name="agent.response",
                role="assistant",
                content=text_content,
                tool_calls=tool_calls,
                game_id=game_id,
            )

        # Update state
        memory = await self.memory.get_memory()
        memory_dicts = [m.to_dict() for m in memory]
        self._state = memory_dicts

        logger.info(
            "agent '%s' processed %d event(s)",
            self.actor_id,
            len(events),
        )

    async def _process_events_with_retry(
        self,
        events: list[StreamEvent[Any]],
        retry_count: int = 0,
    ) -> bool:
        """Process events with retry logic.

        Args:
            events: List of events to process
            retry_count: Current retry attempt (0-based)

        Returns:
            True if processing succeeded, False if failed after all retries
        """
        try:
            await self._process_events(events)
            return True
        except Exception as e:
            if retry_count < self._max_event_retry_count:
                logger.warning(
                    "agent '%s' batch processing failed (attempt %d/%d), retrying: %s",
                    self.actor_id,
                    retry_count + 1,
                    self._max_event_retry_count,
                    e,
                )
                return await self._process_events_with_retry(events, retry_count + 1)
            else:
                logger.error(
                    "agent '%s' batch of %d event(s) failed after %d attempts, "
                    "dropping events: seq=%s",
                    self.actor_id,
                    len(events),
                    self._max_event_retry_count,
                    [ev.sequence for ev in events],
                    exc_info=True,
                )
                return False

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process incoming stream event with the ReActAgent.

        Event handling behavior:
        - Events are formatted to LLM-friendly text based on their type
        - If the agent is busy processing, new events are queued
        - After processing completes, all queued events are consolidated
          into a single message for the next processing cycle
        - Failed events are retried up to max_event_retry_count times

        Emits OTLP trace spans for each agent message:
        - agent.input: User input from stream event(s)
        - agent.response: Assistant response from LLM
        """
        logger.debug(
            "agent '%s' received event seq=%s from stream '%s'",
            self.actor_id,
            event.sequence,
            event.stream_id,
        )

        async with self._processing_lock:
            if self._is_processing:
                # Agent is busy, queue this event with retry_count=0
                self._event_queue.append((event, 0))
                logger.debug(
                    "agent '%s' queued event seq=%s (queue size: %d)",
                    self.actor_id,
                    event.sequence,
                    len(self._event_queue),
                )
                return

            # Mark as processing
            self._is_processing = True

        try:
            # Process the current event with retry
            await self._process_events_with_retry([event])

            # Process any queued events
            while True:
                async with self._processing_lock:
                    if not self._event_queue:
                        break

                    # Take a snapshot of current queue
                    queued_items = list(self._event_queue)
                    self._event_queue.clear()

                # Extract events and find max retry count for the batch
                queued_events = [item[0] for item in queued_items]
                max_retry = max(item[1] for item in queued_items)

                logger.info(
                    "agent '%s' processing %d queued event(s) (max_retry_count=%d)",
                    self.actor_id,
                    len(queued_events),
                    max_retry,
                )
                await self._process_events_with_retry(
                    queued_events, retry_count=max_retry
                )

        except Exception:
            logger.exception(
                "agent '%s' unexpected error in event processing loop",
                self.actor_id,
            )
            raise
        finally:
            async with self._processing_lock:
                self._is_processing = False

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
