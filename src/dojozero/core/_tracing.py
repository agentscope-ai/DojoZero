"""Trace storage and reader interfaces for DojoZero.

This module provides:
- SpanData: Normalized span representation (OTel-compatible)
- TraceReader: Protocol for reading traces from any backend
- DashboardTraceReader: Reads from Dashboard's built-in Trace Query API
- JaegerTraceReader: Reads from Jaeger HTTP API

Unified Span Protocol:
- Resource Spans (*.registered): Actor metadata, emitted once per actor
- Event Spans: Runtime events with business data (event.* tags)
- All data flows through spans, no separate agent_states needed
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

import httpx

LOGGER = logging.getLogger("dojozero.trace_store")


@dataclass(slots=True)
class SpanData:
    """Normalized span data structure (OTel-compatible).

    This is the format used both for storage and transmission to frontend.
    """

    trace_id: str
    span_id: str
    operation_name: str
    start_time: int  # Microseconds since epoch
    duration: int  # Microseconds
    parent_span_id: str | None = None
    tags: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "traceID": self.trace_id,
            "spanID": self.span_id,
            "operationName": self.operation_name,
            "startTime": self.start_time,
            "duration": self.duration,
            "parentSpanID": self.parent_span_id,
            "tags": [{"key": k, "value": v} for k, v in self.tags.items()],
            "logs": self.logs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpanData":
        """Create SpanData from dictionary."""
        tags = {}
        for tag in data.get("tags", []):
            if isinstance(tag, dict):
                tags[tag.get("key", "")] = tag.get("value")
        return cls(
            trace_id=data.get("traceID", ""),
            span_id=data.get("spanID", ""),
            operation_name=data.get("operationName", ""),
            start_time=data.get("startTime", 0),
            duration=data.get("duration", 0),
            parent_span_id=data.get("parentSpanID"),
            tags=tags,
            logs=data.get("logs", []),
        )


class TraceReader(Protocol):
    """Protocol for reading traces from any backend."""

    async def list_trials(self) -> list[str]:
        """List all trial IDs with traces."""
        ...

    async def get_spans(
        self,
        trial_id: str,
        since: datetime | None = None,
    ) -> list[SpanData]:
        """Get spans for a trial."""
        ...


class DashboardTraceReader:
    """TraceReader that reads from Dashboard's built-in Trace Query API."""

    def __init__(self, dashboard_url: str) -> None:
        self._base_url = dashboard_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def list_trials(self) -> list[str]:
        """List all trial IDs from Dashboard."""
        response = await self._client.get(f"{self._base_url}/api/traces")
        response.raise_for_status()
        data = response.json()
        return [item["trial_id"] for item in data]

    async def get_spans(
        self,
        trial_id: str,
        since: datetime | None = None,
    ) -> list[SpanData]:
        """Get spans for a trial from Dashboard."""
        params: dict[str, str] = {}
        if since is not None:
            params["since"] = since.isoformat()
        response = await self._client.get(
            f"{self._base_url}/api/traces/{trial_id}",
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        return [SpanData.from_dict(span) for span in data.get("spans", [])]

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class JaegerTraceReader:
    """TraceReader that reads from Jaeger HTTP API."""

    def __init__(
        self,
        jaeger_url: str,
        service_name: str = "dojozero",
    ) -> None:
        self._base_url = jaeger_url.rstrip("/")
        self._service_name = service_name
        self._client = httpx.AsyncClient(timeout=30.0)

    async def list_trials(self) -> list[str]:
        """List all trial IDs from Jaeger.

        Uses Jaeger's trace search API and extracts unique trial IDs from span tags.
        """
        response = await self._client.get(
            f"{self._base_url}/api/traces",
            params={
                "service": self._service_name,
                "limit": 100,
            },
        )
        response.raise_for_status()
        data = response.json()

        trial_ids: set[str] = set()
        for trace in data.get("data", []):
            for span in trace.get("spans", []):
                for tag in span.get("tags", []):
                    if tag.get("key") == "dojozero.trial.id":
                        trial_ids.add(str(tag.get("value", "")))
        return list(trial_ids)

    async def get_spans(
        self,
        trial_id: str,
        since: datetime | None = None,
    ) -> list[SpanData]:
        """Get spans for a trial from Jaeger."""
        params: dict[str, Any] = {
            "service": self._service_name,
            "tags": f'{{"dojozero.trial.id":"{trial_id}"}}',
            "limit": 1000,
        }
        if since is not None:
            params["start"] = int(since.timestamp() * 1_000_000)

        response = await self._client.get(
            f"{self._base_url}/api/traces",
            params=params,
        )
        response.raise_for_status()
        data = response.json()

        spans: list[SpanData] = []
        for trace in data.get("data", []):
            for span in trace.get("spans", []):
                tags: dict[str, Any] = {}
                for tag in span.get("tags", []):
                    tags[tag.get("key", "")] = tag.get("value")

                spans.append(
                    SpanData(
                        trace_id=span.get("traceID", ""),
                        span_id=span.get("spanID", ""),
                        operation_name=span.get("operationName", ""),
                        start_time=span.get("startTime", 0),
                        duration=span.get("duration", 0),
                        parent_span_id=span.get("references", [{}])[0].get("spanID")
                        if span.get("references")
                        else None,
                        tags=tags,
                        logs=span.get("logs", []),
                    )
                )

        # Sort by start time
        spans.sort(key=lambda s: s.start_time)
        return spans

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


def create_span_from_event(
    trial_id: str,
    actor_id: str,
    operation_name: str,
    start_time: datetime | None = None,
    duration_ms: int = 0,
    parent_span_id: str | None = None,
    extra_tags: dict[str, Any] | None = None,
) -> SpanData:
    """Create a SpanData from event data.

    Helper function to create spans from DojoZero events/operations.
    """
    now = start_time or datetime.now(timezone.utc)
    start_us = int(now.timestamp() * 1_000_000)
    duration_us = duration_ms * 1000

    tags = {
        "dojozero.trial.id": trial_id,
        "dojozero.actor.id": actor_id,
    }
    if extra_tags:
        tags.update(extra_tags)

    return SpanData(
        trace_id=trial_id,  # Use trial_id as trace_id for correlation
        span_id=uuid4().hex[:16],
        operation_name=operation_name,
        start_time=start_us,
        duration=duration_us,
        parent_span_id=parent_span_id,
        tags=tags,
    )


def convert_actor_registration_to_span(
    trial_id: str,
    actor_id: str,
    actor_type: str,
    metadata: dict[str, Any],
    timestamp: datetime | None = None,
) -> SpanData:
    """Convert actor registration to a resource span.

    Resource spans are emitted once per actor and contain metadata about
    the actor (name, model, tools, etc.). Frontend uses these to build
    the actor list and display agent information.

    Args:
        trial_id: The trial ID.
        actor_id: Unique actor identifier.
        actor_type: "agent" or "datastream".
        metadata: Actor metadata (name, model, system_prompt, tools, etc.).
        timestamp: Registration timestamp (defaults to now).

    Returns:
        SpanData with operationName "{actor_type}.registered".
    """
    now = timestamp or datetime.now(timezone.utc)
    start_us = int(now.timestamp() * 1_000_000)

    operation_name = f"{actor_type}.registered"

    tags: dict[str, Any] = {
        "dojozero.trial.id": trial_id,
        "dojozero.actor.id": actor_id,
        "dojozero.actor.type": actor_type,
    }

    # Add resource.* tags from metadata
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            tags[f"resource.{key}"] = json.dumps(value, default=str)
        else:
            tags[f"resource.{key}"] = value

    return SpanData(
        trace_id=trial_id,
        span_id=uuid4().hex[:16],
        operation_name=operation_name,
        start_time=start_us,
        duration=0,
        parent_span_id=None,
        tags=tags,
    )


def convert_checkpoint_event_to_span(
    trial_id: str,
    event: dict[str, Any],
    sequence: int = 0,
    actor_id: str = "unknown",
) -> SpanData:
    """Convert a checkpoint event dict to SpanData format.

    Checkpoint events have format: { event_type: "...", timestamp: "...", ... }
    This converts them to the unified SpanData format.
    """
    event_type = event.get("event_type", "unknown")
    event_actor_id = event.get("actor_id", event.get("stream_id", actor_id))

    # Parse timestamp
    timestamp_str = event.get("timestamp") or event.get("emitted_at")
    if timestamp_str:
        try:
            if isinstance(timestamp_str, str):
                dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            else:
                dt = timestamp_str
            start_us = int(dt.timestamp() * 1_000_000)
        except (ValueError, AttributeError):
            start_us = 0
    else:
        start_us = 0

    # Build tags from event data (exclude metadata fields)
    metadata_fields = {
        "event_type",
        "timestamp",
        "emitted_at",
        "actor_id",
        "stream_id",
        "sequence",
    }
    tags: dict[str, Any] = {
        "dojozero.trial.id": trial_id,
        "dojozero.actor.id": event_actor_id,
        "dojozero.event.type": event_type,
        "dojozero.event.sequence": event.get("sequence", sequence),
    }
    # Add remaining event data as tags
    for key, value in event.items():
        if key not in metadata_fields:
            # Convert complex values to string
            if isinstance(value, (dict, list)):
                tags[f"event.{key}"] = json.dumps(value, default=str)
            else:
                tags[f"event.{key}"] = value

    return SpanData(
        trace_id=trial_id,
        span_id=uuid4().hex[:16],
        operation_name=event_type,
        start_time=start_us,
        duration=0,
        parent_span_id=None,
        tags=tags,
    )


def _extract_text_from_content(content: Any) -> str:
    """Extract text string from various content formats.

    Handles:
    - str: Return as-is
    - list[{"type": "text", "text": "..."}]: Extract and join text items
    - list[{"text": "..."}]: Extract and join text items
    - dict: JSON serialize
    - None/empty: Return empty string
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                # Handle {"type": "text", "text": "..."} format
                if item.get("type") == "text" and "text" in item:
                    texts.append(str(item["text"]))
                # Handle {"text": "..."} format (without type)
                elif "text" in item and "type" not in item:
                    texts.append(str(item["text"]))
        return " ".join(texts) if texts else ""
    if isinstance(content, dict):
        # Single dict with text
        if content.get("type") == "text" and "text" in content:
            return str(content["text"])
        return json.dumps(content, default=str)
    return str(content)


def _extract_tool_calls_from_content(content: Any) -> list[dict] | None:
    """Extract tool calls from content array if present.

    Handles content format: [{"type": "tool_use", "name": "...", ...}, ...]
    """
    if not isinstance(content, list):
        return None
    tool_calls = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            tool_calls.append(item)
    return tool_calls if tool_calls else None


def convert_agent_message_to_span(
    trial_id: str,
    actor_id: str,
    stream_id: str,
    message: dict[str, Any],
    sequence: int = 0,
) -> SpanData | None:
    """Convert an agent conversation message to SpanData format.

    Agent messages have format: { content, role, name, timestamp, id, tool_calls, ... }
    Content can be:
    - str: Plain text
    - list[{"type": "text", "text": "..."}]: Array of content blocks
    - list[{"type": "tool_use", ...}]: Tool calls

    Returns None if the message has no meaningful content to display.
    """
    role = message.get("role", "unknown")
    name = message.get("name", "unknown")
    raw_content = message.get("content")

    # Extract text content from various formats
    content = _extract_text_from_content(raw_content)

    # Extract tool calls from content array or message field
    tool_calls = message.get("tool_calls")
    if tool_calls is None:
        tool_calls = _extract_tool_calls_from_content(raw_content)

    # Skip messages with no text content and no tool calls
    if not content and not tool_calls:
        return None

    # Parse timestamp
    timestamp_str = message.get("timestamp")
    start_us = 0
    if timestamp_str:
        try:
            if isinstance(timestamp_str, str):
                try:
                    dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                except ValueError:
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
                    dt = dt.replace(tzinfo=timezone.utc)
                start_us = int(dt.timestamp() * 1_000_000)
        except (ValueError, AttributeError):
            pass

    # Determine operation name based on role
    operation_name = f"agent.{role}"
    if role == "assistant":
        operation_name = "agent.response"
    elif role == "user":
        operation_name = "agent.input"
    elif role == "system":
        operation_name = "agent.tool_result"

    # Use event.* prefix so frontend spanToEvent can extract fields
    tags: dict[str, Any] = {
        "dojozero.trial.id": trial_id,
        "dojozero.actor.id": actor_id,
        "dojozero.event.type": operation_name,
        "dojozero.event.sequence": sequence,
        "event.stream_id": stream_id,
        "event.role": role,
        "event.name": name,
        "event.content": content,
    }

    # Add message ID if present
    if "id" in message:
        tags["event.message_id"] = message["id"]

    # Add tool_calls if present
    if tool_calls:
        if isinstance(tool_calls, (list, dict)):
            tags["event.tool_calls"] = json.dumps(tool_calls, default=str)
        else:
            tags["event.tool_calls"] = tool_calls

    # Add tool_call_id if present (for tool results)
    if "tool_call_id" in message:
        tags["event.tool_call_id"] = message["tool_call_id"]

    return SpanData(
        trace_id=trial_id,
        span_id=uuid4().hex[:16],
        operation_name=operation_name,
        start_time=start_us,
        duration=0,
        parent_span_id=None,
        tags=tags,
    )


def load_spans_from_checkpoint(
    trial_id: str,
    actor_states: dict[str, Any],
    since_us: int = 0,
) -> list[SpanData]:
    """Load all data from checkpoint actor_states and convert to spans.

    This function implements the unified span protocol where ALL data
    flows through spans:

    1. Resource Spans (*.registered): Actor metadata, emitted once per actor
    2. Event Spans: Runtime events with business data

    Actor types:
    - DataStream: { events: [...], name?, source_type?, ... }
    - Agent: { state: [{ stream_id: [messages...] }], name?, model?, ... }

    Args:
        trial_id: The trial ID.
        actor_states: Dictionary of actor_id -> actor state from checkpoint.
        since_us: Filter spans starting after this timestamp (microseconds).

    Returns:
        List of SpanData sorted by start_time (registration spans first).
    """
    registration_spans: list[SpanData] = []
    event_spans: list[SpanData] = []
    sequence = 0

    for actor_id, actor_state in actor_states.items():
        if not isinstance(actor_state, dict):
            continue

        # Determine actor type and extract metadata
        has_agent_state = "state" in actor_state
        has_events = "events" in actor_state

        if has_agent_state:
            # Agent actor
            actor_type = "agent"
            metadata = {
                "name": actor_state.get("name", actor_id),
                "model": actor_state.get("model"),
                "model_provider": actor_state.get("model_provider"),
                "system_prompt": actor_state.get("system_prompt"),
                "tools": actor_state.get("tools", []),
            }
        elif has_events:
            # DataStream actor
            actor_type = "datastream"
            metadata = {
                "name": actor_state.get("name", actor_id),
                "source_type": actor_state.get("source_type"),
            }
        else:
            # Unknown actor type, skip registration span
            actor_type = "unknown"
            metadata = {"name": actor_state.get("name", actor_id)}

        # 1. Generate registration span for each actor
        reg_span = convert_actor_registration_to_span(
            trial_id, actor_id, actor_type, metadata
        )
        registration_spans.append(reg_span)

        # 2. Convert DataStream events
        events = actor_state.get("events", [])
        if isinstance(events, list):
            for evt in events:
                if not isinstance(evt, dict):
                    continue
                span = convert_checkpoint_event_to_span(
                    trial_id, evt, sequence, actor_id
                )
                if span.start_time >= since_us:
                    event_spans.append(span)
                sequence += 1

        # 3. Convert Agent conversation history (state field)
        state_list = actor_state.get("state", [])
        if isinstance(state_list, list):
            for state_item in state_list:
                if not isinstance(state_item, dict):
                    continue
                # state_item format: { stream_id: [messages...] }
                for stream_id, messages in state_item.items():
                    if not isinstance(messages, list):
                        continue
                    for msg in messages:
                        if not isinstance(msg, dict):
                            continue
                        span = convert_agent_message_to_span(
                            trial_id, actor_id, stream_id, msg, sequence
                        )
                        # Skip empty messages (returns None)
                        if span is not None and span.start_time >= since_us:
                            event_spans.append(span)
                        sequence += 1

    # Sort event spans by start time
    event_spans.sort(key=lambda s: s.start_time)

    # Registration spans come first (at timestamp 0 effectively), then events
    return registration_spans + event_spans


__all__ = [
    "DashboardTraceReader",
    "JaegerTraceReader",
    "SpanData",
    "TraceReader",
    "convert_actor_registration_to_span",
    "convert_agent_message_to_span",
    "convert_checkpoint_event_to_span",
    "create_span_from_event",
    "load_spans_from_checkpoint",
]
