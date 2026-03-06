"""Generic betting agent implementation.

This module provides a BettingAgent class that uses ReActAgent for LLM reasoning
in sports betting scenarios. It's sport-agnostic and can be used with any
betting scenario (NBA, NFL, etc.) by providing appropriate formatters.
"""

import asyncio
import inspect
import json
import logging
from collections import deque
from datetime import datetime, timedelta
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
from dojozero.betting._config import MEMORY_SUMMARY_PROMPT
from dojozero.data._models import DataEvent, EventTypes, extract_game_id
from dojozero.betting._models import (
    ReasoningStep,
    ToolCallStep,
    ToolResultStep,
    CoTStep,
    AgentResponseMessage,
    BetExecutedPayload,
    BetSettledPayload,
)

logger = logging.getLogger(__name__)


# Type for event formatter function
EventFormatter = Callable[[DataEvent | BetExecutedPayload | BetSettledPayload], str]


def _is_formattable_payload(payload: Any) -> bool:
    """Check if payload can be formatted by event formatter."""
    return isinstance(payload, (DataEvent, BetExecutedPayload, BetSettledPayload))


class _ActorIdConfig(TypedDict):
    actor_id: str


class BettingAgentConfig(_ActorIdConfig, total=False):
    """Betting agent configuration.

    Supports two modes:
    1. Config paths: Load config from separate persona and LLM YAML files
    2. Inline config: Use llm field with LLMConfig
    """

    name: str
    sys_prompt: str
    tools: list[str]  # List of tool names to enable
    llm: LLMConfig  # LLM configuration
    persona_config_path: str  # Path to persona YAML config file
    llm_config_path: str  # Path to LLM YAML config file


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


def _extract_memory_diff(before: list[dict], after: list[dict]) -> list[dict]:
    """Get only new messages from this turn by comparing memory before/after.

    Returns:
        List of new messages added during this turn
    """
    before_len = len(before)
    return after[before_len:]


def _parse_cot_steps(messages: list[dict]) -> list[CoTStep]:
    """Parse message diffs into CoT steps (tool calls, results, reasoning).

    Args:
        messages: List of new messages from this turn

    Returns:
        List of CoTStep objects representing the chain of thought
    """
    steps: list[CoTStep] = []

    for msg in messages:
        content = msg.get("content", [])
        role = msg.get("role", "")

        # Handle string content (reasoning text)
        if isinstance(content, str) and content.strip():
            if role == "assistant":
                steps.append(ReasoningStep(text=content.strip()))
            continue

        if not isinstance(content, list):
            continue

        for item in content:
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")

            if item_type == "text":
                # Assistant reasoning text
                text = item.get("text", "").strip()
                if text and role == "assistant":
                    steps.append(ReasoningStep(text=text))

            elif item_type == "tool_use":
                # Tool call
                tool_name = item.get("name", "unknown")
                tool_input = item.get("input", {})

                # Format input as "key=value" pairs for display
                if isinstance(tool_input, dict):
                    input_parts = [f"{k}={v}" for k, v in tool_input.items()]
                    input_display = ", ".join(input_parts)
                else:
                    input_display = str(tool_input)

                steps.append(ToolCallStep(name=tool_name, input_display=input_display))

            elif item_type == "tool_result":
                # Tool result
                tool_name = item.get("name", "unknown")
                tool_output = item.get("output", [])

                # Format output for display
                if isinstance(tool_output, list):
                    output_texts = []
                    for out_item in tool_output:
                        if (
                            isinstance(out_item, dict)
                            and out_item.get("type") == "text"
                        ):
                            output_texts.append(out_item.get("text", ""))
                    output_display = (
                        " ".join(output_texts) if output_texts else str(tool_output)
                    )
                else:
                    output_display = str(tool_output)

                steps.append(
                    ToolResultStep(name=tool_name, output_display=output_display)
                )

    return steps


def _extract_bet_from_tool_calls(messages: list[dict]) -> dict[str, Any] | None:
    """Detect place_*_bet_* tool calls and extract bet info.

    Supports six tool variants (market and limit for each bet type):
    - place_market_bet_moneyline / place_limit_bet_moneyline: MONEYLINE bet
    - place_market_bet_spread / place_limit_bet_spread: SPREAD bet
    - place_market_bet_total / place_limit_bet_total: TOTAL bet

    Args:
        messages: List of new messages from this turn

    Returns:
        Dict with bet fields if bet placed, None otherwise
    """
    # Mapping of tool names to (bet_type, order_type)
    bet_tool_mapping: dict[str, tuple[str, str]] = {
        "place_market_bet_moneyline": ("MONEYLINE", "MARKET"),
        "place_limit_bet_moneyline": ("MONEYLINE", "LIMIT"),
        "place_market_bet_spread": ("SPREAD", "MARKET"),
        "place_limit_bet_spread": ("SPREAD", "LIMIT"),
        "place_market_bet_total": ("TOTAL", "MARKET"),
        "place_limit_bet_total": ("TOTAL", "LIMIT"),
    }

    for msg in messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue

        for item in content:
            if not isinstance(item, dict):
                continue

            if item.get("type") != "tool_use":
                continue

            tool_name = item.get("name", "")
            if tool_name not in bet_tool_mapping:
                continue

            tool_input = item.get("input", {})
            if not isinstance(tool_input, dict):
                continue

            # Extract bet type and order type from tool name
            bet_type, order_type = bet_tool_mapping[tool_name]
            amount = tool_input.get("amount", 0)

            result: dict[str, Any] = {
                "bet_type": bet_type,
                "bet_amount": float(amount) if amount else 0.0,
                "bet_selection": tool_input.get("selection", "home"),
                "bet_order_type": order_type,
            }

            # Add limit_probability for LIMIT orders
            if order_type == "LIMIT":
                limit_prob = tool_input.get("limit_probability")
                if limit_prob is not None:
                    result["bet_limit_probability"] = float(limit_prob)

            # Add type-specific fields
            if bet_type == "SPREAD":
                spread_value = tool_input.get("spread_value")
                if spread_value is not None:
                    result["bet_spread_value"] = float(spread_value)
            elif bet_type == "TOTAL":
                total_value = tool_input.get("total_value")
                if total_value is not None:
                    result["bet_total_value"] = float(total_value)
                # Default selection for total bets
                if "selection" not in tool_input:
                    result["bet_selection"] = "over"

            return result

    return None


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
        self._retry_sleep_time = 3
        # Event formatter for converting DataEvents to LLM-friendly text
        self._event_formatter = event_formatter or _default_format_event

        # Memory compression settings
        self._event_history: deque[str] = deque(maxlen=1000)
        self._compressed_context: str | None = None
        self._memory_token_threshold: int = 9000
        self._head_events: int = 10
        self._tail_events: int = 10
        self._max_event_chars: int = 200

        # Throttle settings for odds updates (at most once per cooldown period)
        self._odds_update_cooldown = timedelta(minutes=3)
        self._last_odds_update_time: datetime | None = None

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

    def _truncate_event(self, text: str) -> str:
        """Truncate event text to max chars."""
        if len(text) <= self._max_event_chars:
            return text
        return text[: self._max_event_chars - 3] + "..."

    def _build_events_summary(self) -> str:
        """Build a sparse summary of event history.

        Returns first N + last N events with ellipsis in between if too many.
        """
        events = list(self._event_history)
        if not events:
            return ""

        total = len(events)
        head_n = self._head_events
        tail_n = self._tail_events

        if total <= head_n + tail_n:
            # Few events, keep all
            return "\n".join(events)

        # Sparse: first N + ellipsis + last N
        head = events[:head_n]
        tail = events[-tail_n:]
        skipped = total - head_n - tail_n

        return "\n".join(
            [
                *head,
                f"... ({skipped} events omitted) ...",
                *tail,
            ]
        )

    def _estimate_memory_tokens(self, messages: list[dict]) -> int:
        """Rough token estimate for a list of memory messages."""
        return len(json.dumps(messages, default=str)) // 4

    async def _get_bet_history_summary(self) -> str:
        """Get bet history from broker operator if available."""
        for op in self._operator_registry.values():
            if hasattr(op, "get_bet_history"):
                try:
                    history = await op.get_bet_history(self.actor_id, limit=50)  # type: ignore[attr-defined]
                    if history:
                        # Format bets simply: last 20 bets
                        summaries = []
                        for bet in history[-20:]:
                            outcome_str = (
                                f" -> {bet.outcome.value}" if bet.outcome else ""
                            )
                            summaries.append(
                                f"{bet.selection}: {bet.amount} @ probability {bet.probability} (shares: {bet.shares}){outcome_str} [{bet.status.value}]"
                            )
                        return "\n".join(summaries)
                except Exception as e:
                    logger.warning("Failed to get bet history: %s", e)
        return "No betting history available."

    def _build_summary_request(self) -> list[dict] | None:
        """Build the LLM summarization request from event history.

        Returns None if there is nothing to summarize.
        """
        events = list(self._event_history)
        if not events:
            return None

        transcript = "\n".join(events)
        if not transcript:
            return None

        return [
            {
                "role": "user",
                "content": (
                    f"{MEMORY_SUMMARY_PROMPT}\n\n"
                    "---BEGIN CONVERSATION---\n"
                    f"{transcript}\n"
                    "---END CONVERSATION---\n\n"
                    "Provide the compressed summary:"
                ),
            }
        ]

    def _parse_summary_response(self, resp: Any) -> str:
        """Extract plain text from an LLM summary response."""
        text_parts = [
            block.get("text", "")
            for block in resp.content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(filter(None, text_parts)).strip()

    async def _offload(self) -> None:
        """Compress memory using LLM summarization, with sparse-summary fallback.

        Strategy:
        1. Capture current ReActAgent memory as a transcript
        2. Call LLM to produce a structured summary (pre-game analysis, game
           progress, betting record, market context)
        3. Fall back to the sparse first-N/last-N event summary on LLM failure
        4. Append broker bet history and store as _compressed_context
        5. Clear ReActAgent memory

        Note: Events are added to _event_history in _process_events BEFORE
        prepending compressed context, to avoid nesting/duplication.
        """

        # try:
        summary_request = self._build_summary_request()
        if summary_request:
            model = self._react_agent.model
            original_stream = model.stream
            model.stream = False
            try:
                resp = await model(summary_request)
            finally:
                model.stream = original_stream
            events_summary = self._parse_summary_response(resp)
        else:
            events_summary = ""
        summary_label = "[Memory Summary]"
        # if not events_summary:
        #     raise ValueError("LLM returned empty summary")
        # except Exception as e:
        #     logger.warning(
        #         "agent '%s' LLM summarization failed, using sparse fallback: %s",
        #         self.actor_id,
        #         e,
        #     )
        #     events_summary = self._build_events_summary()
        #     summary_label = "[Historical Event Summary]"

        bet_history = await self._get_bet_history_summary()

        self._compressed_context = (
            f"{summary_label}\n"
            f"{events_summary}\n\n"
            "[Your Betting History]\n"
            f"{bet_history}"
        )
        print("*********compressed_context*********\n")
        print(self._compressed_context)
        print("*********compressed_context*********\n")
        # Clear the ReActAgent's memory
        self._react_agent.memory = InMemoryMemory()

        logger.info(
            "agent '%s' offloaded memory: %d events in history, compressed to %d chars",
            self.actor_id,
            len(self._event_history),
            len(self._compressed_context),
        )

    @classmethod
    def from_dict(
        cls,
        config: BettingAgentConfig,
        context: RuntimeContext,
    ) -> "BettingAgent":
        """Create agent from config dict.

        Note: persona_config_path and llm_config_path are no longer supported here -
        the trial builder handles YAML loading and expansion. This method expects
        inline configs with a single LLMConfig.
        """
        actor_id = config["actor_id"]

        # Inline config mode (already expanded by trial builder)
        llm_config = config.get("llm", {})
        if not llm_config:
            raise ValueError(f"Missing 'llm' config for agent {actor_id}")
        model_type = llm_config.get("model_type", "openai")
        model_name = llm_config.get("model_name", "")
        return cls(
            actor_id=actor_id,
            trial_id=context.trial_id,
            name=config.get("name", actor_id),
            sys_prompt=config.get("sys_prompt", ""),
            model=create_model(llm_config),
            formatter=create_formatter(model_type, model_name),
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
            # Only DataEvent contains game_id information
            if isinstance(payload, DataEvent):
                game_id = extract_game_id(payload.to_dict())
                if game_id:
                    return game_id
        return ""

    def _emit_input_span(
        self,
        stream_id: str,
        content: str,
        game_id: str = "",
    ) -> None:
        """Emit a span for agent input to the OTel exporter."""
        tags: dict[str, Any] = {
            "sequence": self._event_count,
            "event.stream_id": stream_id,
            "event.role": "user",
            "event.name": self.name,
            "event.content": content,
            "game.id": game_id,
        }

        span = create_span_from_event(
            trial_id=self.trial_id,
            actor_id=self.actor_id,
            operation_name="agent.input",
            extra_tags=tags,
        )
        emit_span(span)

    def _emit_response_span(self, message: AgentResponseMessage) -> None:
        """Emit a span for agent response to the OTel exporter.

        Args:
            message: Structured agent response message (includes bet fields if present)
        """
        # Build tags from message, excluding None values (e.g., empty bet fields)
        tags = message.model_dump(exclude_none=True)

        # Serialize cot_steps to JSON string for proper display in tracing UI
        if "cot_steps" in tags:
            tags["cot_steps"] = json.dumps(tags["cot_steps"])

        span = create_span_from_event(
            trial_id=self.trial_id,
            actor_id=self.actor_id,
            operation_name="agent.response",
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
            if _is_formattable_payload(payload):
                return self._event_formatter(payload)
            else:
                return f"[New data]: {json.dumps(payload, default=str, ensure_ascii=False)}"

        # Multiple events: consolidate with headers
        lines = [f"[{len(events)} New Events Received]\n"]

        for event in events:
            payload = event.payload
            if _is_formattable_payload(payload):
                formatted = self._event_formatter(payload)
            else:
                formatted = (
                    f"[Data]: {json.dumps(payload, default=str, ensure_ascii=False)}"
                )

            lines.append(formatted)

        return "\n".join(lines)

    def _filter_throttled_events(
        self, events: list[StreamEvent[Any]]
    ) -> list[StreamEvent[Any]]:
        """Filter out events that should be throttled.

        Currently throttles ODDS_UPDATE events to at most once per cooldown period.
        Uses event timestamps (not wall-clock time) so backtest replay works correctly.

        Args:
            events: List of events to filter

        Returns:
            Filtered list of events (throttled events removed)
        """
        filtered: list[StreamEvent[Any]] = []

        for event in events:
            payload = event.payload

            # Check if this is an OddsUpdateEvent that should be throttled
            if isinstance(payload, DataEvent):
                event_type = getattr(payload, "event_type", None)
                if event_type == EventTypes.ODDS_UPDATE:
                    # Use event timestamp for cooldown (works for both live and backtest)
                    event_time = payload.timestamp
                    if (
                        self._last_odds_update_time
                        and (event_time - self._last_odds_update_time)
                        < self._odds_update_cooldown
                    ):
                        logger.debug(
                            "agent '%s' throttling odds_update (last update: %s ago)",
                            self.actor_id,
                            event_time - self._last_odds_update_time,
                        )
                        continue  # Skip this event
                    # Update last processed time with event timestamp
                    self._last_odds_update_time = event_time

            filtered.append(event)

        return filtered

    async def _process_events(self, events: list[StreamEvent[Any]]) -> None:
        """Process a batch of events.

        Args:
            events: List of events to process together
        """
        if not events:
            return

        # Apply throttling filter BEFORE any processing
        events = self._filter_throttled_events(events)
        if not events:
            logger.debug("agent '%s' all events throttled, skipping", self.actor_id)
            return

        # Update event count
        self._event_count += len(events)

        # update event history
        for event in events:
            payload = event.payload
            if _is_formattable_payload(payload):
                formatted = self._event_formatter(payload)
            else:
                formatted = (
                    f"[Data]: {json.dumps(payload, default=str, ensure_ascii=False)}"
                )
            truncated = self._truncate_event(formatted)
            self._event_history.append(truncated)

        # Format events for LLM
        input_content = self._format_events_for_llm(events)

        # If we have compressed context, prepend it to the current event input
        # (as part of the same user message to maintain user-assistant pairing)
        if self._compressed_context:
            input_content = (
                f"{self._compressed_context}\n\n[New Events]\n{input_content}"
            )
            # Clear compressed context after use
            self._compressed_context = None

        # Log event processing with stream count summary
        logger.info(
            "agent '%s' processing %d event(s) from streams: {%s}",
            self.actor_id,
            len(events),
            input_content[:500],  # Truncate for logging
        )

        msg = Msg(name="event_push", content=input_content, role="user")

        # Use first event's stream_id for tracing
        primary_stream_id = events[0].stream_id
        game_id = self._extract_game_id_from_events(events)
        self._emit_input_span(
            stream_id=primary_stream_id,
            content=input_content,
            game_id=game_id,
        )

        # Capture memory state BEFORE agent call
        memory_before = await self.memory.get_memory()
        memory_before_dicts = [m.to_dict() for m in memory_before]

        # Call agent
        response = await self._react_agent(msg)

        # Capture memory state AFTER agent call
        memory_after = await self.memory.get_memory()
        memory_after_dicts = [m.to_dict() for m in memory_after]
        self._state = memory_after_dicts

        # Get only this turn's new messages
        turn_messages = _extract_memory_diff(memory_before_dicts, memory_after_dicts)

        # Emit span for agent response
        if response is not None:
            response_content = getattr(response, "content", None)
            text_content, _ = _parse_response_content(response_content)

            # Parse CoT steps and bet info from this turn's messages
            cot_steps = _parse_cot_steps(turn_messages)
            bet = _extract_bet_from_tool_calls(turn_messages)

            # Build trigger safely (empty string if no events)
            trigger = ""
            if events:
                last_payload = events[-1].payload
                if _is_formattable_payload(last_payload):
                    trigger = self._event_formatter(last_payload)

            # Emit agent response span with structured message (includes bet fields if present)
            response_message = AgentResponseMessage(
                sequence=self._event_count,
                stream_id=primary_stream_id,
                agent_id=self.actor_id,
                content=text_content,
                cot_steps=cot_steps,
                trigger=trigger,
                game_id=game_id,
                # Bet fields - None if no bet, otherwise populated from bet dict
                **(bet or {}),
            )
            self._emit_response_span(response_message)
        # Compact memory for next iteration when context becomes too large
        if (
            self._estimate_memory_tokens(memory_after_dicts)
            >= self._memory_token_threshold
        ):
            await self._offload()

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
                await asyncio.sleep(self._retry_sleep_time)
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

            # Mark as processing and queue the current event
            self._is_processing = True

            # Skip processing if game is finished (only check DataEvent for game state)
            payload = event.payload
            if (
                isinstance(payload, DataEvent)
                and hasattr(payload, "winner")
                and hasattr(payload, "final_score")
            ):
                logger.info(
                    "agent '%s' skipping event processing - game finished",
                    self.actor_id,
                )
                if _is_formattable_payload(payload):
                    self._event_history.append(self._event_formatter(payload))
                self._is_processing = False
                return

            # Add current event to queue with retry_count=0
            self._event_queue.append((event, 0))

        try:
            # Process all queued events (including the current one)
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
