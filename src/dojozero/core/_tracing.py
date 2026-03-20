"""Trace storage and reader interfaces for DojoZero.

This module provides:
- SpanData: Normalized span representation (OTel-compatible)
- TraceReader: Protocol for reading traces from any backend
- JaegerTraceReader: Reads from Jaeger HTTP API

Unified Span Protocol:
- Resource Spans (*.registered): Actor metadata, emitted once per actor
- Event Spans: Runtime events with business data (event.* tags)
- All data flows through spans, no separate agent_states needed
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4
import asyncio

import httpx

LOGGER = logging.getLogger("dojozero.trace_store")


@dataclass(slots=True)
class SpanData:
    """Normalized span data structure (OTel-compatible).

    This is the format used both for storage and transmission to arena UI.
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

    async def list_trials(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 500,
    ) -> list[str]:
        """List trial IDs with traces.

        Args:
            start_time: Start of time range (inclusive). Defaults to 7 days ago.
            end_time: End of time range (inclusive). Defaults to now.
            limit: Maximum number of trials to return.

        Returns:
            List of unique trial IDs.
        """
        ...

    async def get_spans(
        self,
        trial_id: str,
        start_time: datetime | None = None,
        operation_names: list[str] | None = None,
    ) -> list[SpanData]:
        """Get spans for a trial.

        Args:
            trial_id: The trial ID to get spans for.
            start_time: If provided, only return spans with start_time > this value.
            operation_names: If provided, only return spans with operation_name in this list.
                             Exact match with OR logic. None means no filtering.
        """
        ...

    async def get_all_spans(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        operation_names: list[str] | None = None,
        max_concurrency: int = 10,
    ) -> list[SpanData]:
        """Get all spans without filtering by trial_id.

        Fetches spans by day in parallel for better performance.

        Args:
            start_time: Start of time range. Defaults to 7 days ago.
            end_time: End of time range. Defaults to now.
            operation_names: If provided, only return spans with operation_name in this list.
            max_concurrency: Maximum number of parallel day fetches. Defaults to 10.

        Returns:
            List of SpanData from all trials in the time range.
        """
        ...


class JaegerTraceReader:
    """TraceReader that reads from Jaeger HTTP API."""

    # Default lookback period for trial listing (7 days)
    DEFAULT_LOOKBACK_DAYS = 7

    def __init__(
        self,
        jaeger_url: str,
        service_name: str = "dojozero",
    ) -> None:
        self._base_url = jaeger_url.rstrip("/")
        self._service_name = service_name
        self._client = httpx.AsyncClient(timeout=30.0)

    async def list_trials(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 500,
    ) -> list[str]:
        """List trial IDs from Jaeger by querying trial.started spans.

        This efficiently finds trials by filtering on operation_name="trial.started"
        instead of exhaustively searching all spans.

        Args:
            start_time: Start of time range (inclusive). Defaults to 7 days ago.
            end_time: End of time range (inclusive). Defaults to now.
            limit: Maximum number of traces to return. Defaults to 500.

        Returns:
            List of unique trial IDs found in the time range.
        """
        # Calculate time range in microseconds (Jaeger API uses microseconds)
        now = datetime.now(timezone.utc)

        if end_time is None:
            end_time = now
        end_us = int(end_time.timestamp() * 1_000_000)

        if start_time is not None:
            start_us = int(start_time.timestamp() * 1_000_000)
        else:
            # Default to 7 days ago
            start_dt = now - timedelta(days=self.DEFAULT_LOOKBACK_DAYS)
            start_us = int(start_dt.timestamp() * 1_000_000)

        # Query Jaeger for trial.started spans only
        # This is much more efficient than searching all spans
        params: dict[str, Any] = {
            "service": self._service_name,
            "operation": "trial.started",  # Filter by operation name
            "start": start_us,
            "end": end_us,
            "limit": limit,
        }

        response = await self._client.get(
            f"{self._base_url}/api/traces",
            params=params,
        )
        response.raise_for_status()
        data = response.json()

        # Extract trial IDs from trial.started spans
        trial_ids: set[str] = set()
        for trace in data.get("data", []):
            for span in trace.get("spans", []):
                # Only process trial.started spans (should be all of them due to filter)
                if span.get("operationName") == "trial.started":
                    for tag in span.get("tags", []):
                        if tag.get("key") == "dojozero.trial.id":
                            trial_ids.add(str(tag.get("value", "")))
                            break
        return list(trial_ids)

    async def get_spans(
        self,
        trial_id: str,
        start_time: datetime | None = None,
        operation_names: list[str] | None = None,
    ) -> list[SpanData]:
        """Get spans for a trial from Jaeger.

        Args:
            trial_id: The trial ID to get spans for.
            start_time: If provided, only return spans with start_time > this value
                        (filtered client-side as Jaeger API doesn't support this).
            operation_names: If provided, only return spans with operation_name in this list.
                             Makes separate requests per operation and merges results.
        """
        # If operation_names is provided, make separate requests and merge
        if operation_names:
            all_spans: list[SpanData] = []
            for op_name in operation_names:
                spans = await self._get_spans_for_operation(
                    trial_id, start_time, op_name
                )
                all_spans.extend(spans)
            # Sort by start time
            all_spans.sort(key=lambda s: s.start_time)
            return all_spans
        else:
            return await self._get_spans_for_operation(trial_id, start_time, None)

    async def _get_spans_for_operation(
        self,
        trial_id: str,
        start_time: datetime | None,
        operation_name: str | None,
    ) -> list[SpanData]:
        """Get spans for a trial with optional single operation filter."""
        tags_json = json.dumps({"dojozero.trial.id": trial_id})

        # Calculate time range for the query (default: last 30 days to now)
        now = datetime.now(timezone.utc)
        end_us = int(now.timestamp() * 1_000_000)
        start_query_dt = now - timedelta(days=30)
        start_us = int(start_query_dt.timestamp() * 1_000_000)

        params: dict[str, Any] = {
            "service": self._service_name,
            "tags": tags_json,
            "start": start_us,
            "end": end_us,
            "limit": 1000,
        }

        # Add operation filter if specified
        if operation_name:
            params["operation"] = operation_name

        response = await self._client.get(
            f"{self._base_url}/api/traces",
            params=params,
        )
        response.raise_for_status()
        data = response.json()

        # Convert start_time to microseconds for comparison
        start_time_us = int(start_time.timestamp() * 1_000_000) if start_time else 0

        spans: list[SpanData] = []
        for trace in data.get("data", []):
            for span in trace.get("spans", []):
                span_start_time = span.get("startTime", 0)

                # Filter by start_time (client-side filtering)
                if start_time_us > 0 and span_start_time <= start_time_us:
                    continue

                tags: dict[str, Any] = {}
                for tag in span.get("tags", []):
                    tags[tag.get("key", "")] = tag.get("value")

                spans.append(
                    SpanData(
                        trace_id=span.get("traceID", ""),
                        span_id=span.get("spanID", ""),
                        operation_name=span.get("operationName", ""),
                        start_time=span_start_time,
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

    async def get_all_spans(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        operation_names: list[str] | None = None,
        max_concurrency: int = 10,
    ) -> list[SpanData]:
        """Get all spans without filtering by trial_id.

        Fetches spans by day in parallel for better performance.

        Args:
            start_time: Start of time range. Defaults to 7 days ago.
            end_time: End of time range. Defaults to now.
            operation_names: If provided, only return spans with operation_name in this list.
            max_concurrency: Maximum number of parallel day fetches. Defaults to 10.

        Returns:
            List of SpanData from all trials in the time range.
        """

        now = datetime.now(timezone.utc)
        if end_time is None:
            end_time = now
        if start_time is None:
            start_time = now - timedelta(days=self.DEFAULT_LOOKBACK_DAYS)

        # Split time range into days
        day_ranges: list[tuple[datetime, datetime]] = []
        current = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
        while current < end_time:
            day_end = min(current + timedelta(days=1), end_time)
            day_start = max(current, start_time)
            day_ranges.append((day_start, day_end))
            current += timedelta(days=1)

        LOGGER.info(
            "Jaeger get_all_spans: fetching %d days in parallel (max_concurrency=%d)",
            len(day_ranges),
            max_concurrency,
        )

        # Fetch days in parallel with semaphore
        semaphore = asyncio.Semaphore(max_concurrency)
        completed = [0]
        total_days = len(day_ranges)

        async def fetch_day(day_start: datetime, day_end: datetime) -> list[SpanData]:
            async with semaphore:
                result = await self._fetch_spans_for_day(
                    day_start, day_end, operation_names
                )
                completed[0] += 1
                if completed[0] % 10 == 0 or completed[0] == total_days:
                    LOGGER.info(
                        "Jaeger fetch progress: %d/%d days", completed[0], total_days
                    )
                return result

        tasks = [fetch_day(ds, de) for ds, de in day_ranges]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Merge results
        all_spans: list[SpanData] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                LOGGER.warning("Jaeger fetch failed for day %d: %s", i, result)
            else:
                all_spans.extend(result)

        all_spans.sort(key=lambda s: s.start_time)
        LOGGER.info(
            "Jaeger get_all_spans: total %d spans from %d days",
            len(all_spans),
            total_days,
        )
        return all_spans

    async def _fetch_spans_for_day(
        self,
        day_start: datetime,
        day_end: datetime,
        operation_names: list[str] | None,
    ) -> list[SpanData]:
        """Fetch all spans for a single day."""
        start_us = int(day_start.timestamp() * 1_000_000)
        end_us = int(day_end.timestamp() * 1_000_000)

        if operation_names:
            all_spans: list[SpanData] = []
            for op_name in operation_names:
                spans = await self._query_jaeger_spans(start_us, end_us, op_name)
                all_spans.extend(spans)
            return all_spans
        return await self._query_jaeger_spans(start_us, end_us, None)

    async def _query_jaeger_spans(
        self,
        start_us: int,
        end_us: int,
        operation_name: str | None,
    ) -> list[SpanData]:
        """Query Jaeger API for spans in a time range."""
        params: dict[str, Any] = {
            "service": self._service_name,
            "start": start_us,
            "end": end_us,
            "limit": 5000,
        }
        if operation_name:
            params["operation"] = operation_name

        try:
            response = await self._client.get(
                f"{self._base_url}/api/traces", params=params
            )
            response.raise_for_status()
            data = response.json()

            spans: list[SpanData] = []
            for trace in data.get("data", []):
                for span in trace.get("spans", []):
                    tags = {
                        tag.get("key", ""): tag.get("value")
                        for tag in span.get("tags", [])
                    }
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
            return spans
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            LOGGER.warning("Jaeger query error: %s", e)
            return []

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


def create_trace_reader(
    backend: str,
    trace_query_endpoint: str | None = None,
    service_name: str = "dojozero",
) -> TraceReader:
    """Factory function to create a TraceReader for the Jaeger backend.

    Args:
        backend: Backend type (only "jaeger" is supported).
        trace_query_endpoint: Jaeger Query API endpoint (default: http://localhost:16686).
        service_name: Service name for filtering.

    Returns:
        JaegerTraceReader instance.
    """
    if backend != "jaeger":
        raise ValueError(
            f"Unsupported trace backend: {backend}. Only 'jaeger' is supported."
        )
    jaeger_url = trace_query_endpoint or "http://localhost:16686"
    LOGGER.info("Creating Jaeger trace reader: %s", jaeger_url)
    return JaegerTraceReader(
        jaeger_url=jaeger_url,
        service_name=service_name,
    )


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

    tags: dict[str, Any] = {
        "actor.id": actor_id,
    }
    if extra_tags:
        tags.update(extra_tags)
    # Jaeger/OTLP assigns its own trace id; Arena and tag queries use this tag.
    tags["dojozero.trial.id"] = trial_id

    return SpanData(
        trace_id=trial_id,  # Logical correlation (may differ from Jaeger traceID)
        span_id=uuid4().hex[:16],
        operation_name=operation_name,
        start_time=start_us,
        duration=duration_us,
        parent_span_id=parent_span_id,
        tags=tags,
    )


def deserialize_event_from_span(span: SpanData) -> Any:
    """Reconstruct a DataEvent from a SpanData's tags.

    Reverses the serialization done by DataHub._emit_event_span():
    - operation_name → event_type
    - event.* tags → event fields (JSON-parsed for complex values)

    Args:
        span: SpanData with operation_name as event_type and event.* tags

    Returns:
        A typed DataEvent instance, or None if the event_type is unrecognized
    """
    import json

    from dojozero.data import deserialize_data_event

    event_dict: dict[str, Any] = {"event_type": span.operation_name}
    for key, value in span.tags.items():
        if not key.startswith("event."):
            continue
        field_name = key[6:]  # Remove "event." prefix
        if isinstance(value, str):
            # Try JSON parse for complex fields (dicts, lists)
            if value.startswith(("{", "[")):
                try:
                    event_dict[field_name] = json.loads(value)
                except json.JSONDecodeError:
                    event_dict[field_name] = value
            else:
                event_dict[field_name] = value
        else:
            event_dict[field_name] = value
    return deserialize_data_event(event_dict)


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
        "actor.id": actor_id,
        "actor.type": actor_type,
        "dojozero.trial.id": trial_id,
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
        "actor.id": event_actor_id,
        "sequence": event.get("sequence", sequence),
        "dojozero.trial.id": trial_id,
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

    # Use event.* prefix so arena UI spanToEvent can extract fields
    tags: dict[str, Any] = {
        "actor.id": actor_id,
        "sequence": sequence,
        "dojozero.trial.id": trial_id,
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
    start_time_us: int = 0,
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
        start_time_us: Filter spans starting after this timestamp (microseconds).

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
                if span.start_time >= start_time_us:
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
                        if span is not None and span.start_time >= start_time_us:
                            event_spans.append(span)
                        sequence += 1

    # Sort event spans by start time
    event_spans.sort(key=lambda s: s.start_time)

    # Registration spans come first (at timestamp 0 effectively), then events
    return registration_spans + event_spans


class OTelSpanExporter:
    """OpenTelemetry span exporter using SDK BatchSpanProcessor.

    This class wraps the OpenTelemetry SDK to export DojoZero spans to an OTLP
    endpoint (e.g., Jaeger). Uses BatchSpanProcessor for
    efficient batched HTTP exports.

    Usage:
        exporter = OTelSpanExporter(
            otlp_endpoint="http://localhost:4318",
            service_name="dojozero",
        )
        exporter.start()  # Initialize SDK
        exporter.export_span(span_data)  # Non-blocking (queued by SDK)
        exporter.shutdown()  # Flush remaining and stop
    """

    # Class-level counters for progress logging
    _export_count: int = 0
    _export_error_count: int = 0

    # Cached OTel imports (initialized once)
    _SpanKind: Any = None
    _Status: Any = None
    _StatusCode: Any = None

    def __init__(
        self,
        otlp_endpoint: str,
        service_name: str = "dojozero",
        headers: dict[str, str] | None = None,
        batch_size: int = 512,
        export_timeout_ms: int = 30000,
        schedule_delay_ms: int = 5000,
    ) -> None:
        """Initialize the OTLP exporter.

        Args:
            otlp_endpoint: OTLP HTTP endpoint URL (e.g., http://localhost:4318)
            service_name: Service name for trace attribution
            headers: Optional headers for authentication
            batch_size: Max spans per batch export (BatchSpanProcessor config)
            export_timeout_ms: Timeout for each export request
            schedule_delay_ms: Delay between batch exports
        """
        self._endpoint = otlp_endpoint.rstrip("/")
        self._service_name = service_name
        self._headers = headers
        self._batch_size = batch_size
        self._export_timeout_ms = export_timeout_ms
        self._schedule_delay_ms = schedule_delay_ms
        self._tracer: Any = None
        self._provider: Any = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Lazy initialization of OpenTelemetry components."""
        if self._initialized:
            return

        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.trace import SpanKind, Status, StatusCode

            # Cache imports at class level for reuse
            OTelSpanExporter._SpanKind = SpanKind
            OTelSpanExporter._Status = Status
            OTelSpanExporter._StatusCode = StatusCode

            # Create resource with service name
            resource = Resource.create({"service.name": self._service_name})

            # Create tracer provider
            self._provider = TracerProvider(resource=resource)

            # Create OTLP exporter - use /v1/traces endpoint
            # For Jaeger: http://localhost:4318/v1/traces
            # For Jaeger: http://localhost:4318/v1/traces
            if "log.aliyuncs.com" in self._endpoint:
                traces_endpoint = f"{self._endpoint}/opentelemetry/v1/traces"
            else:
                traces_endpoint = f"{self._endpoint}/v1/traces"

            otlp_exporter = OTLPSpanExporter(
                endpoint=traces_endpoint,
                headers=self._headers,
            )

            # Use BatchSpanProcessor for efficient batched HTTP exports
            batch_processor = BatchSpanProcessor(
                otlp_exporter,
                max_queue_size=2048,
                max_export_batch_size=self._batch_size,
                export_timeout_millis=self._export_timeout_ms,
                schedule_delay_millis=self._schedule_delay_ms,
            )
            self._provider.add_span_processor(batch_processor)

            # Set as global tracer provider
            trace.set_tracer_provider(self._provider)

            # Get tracer
            self._tracer = trace.get_tracer("dojozero.dashboard")
            self._initialized = True

            LOGGER.info(
                "OTel exporter initialized: endpoint=%s service=%s headers=%s "
                "batch_size=%d",
                traces_endpoint,
                self._service_name,
                "present" if self._headers else "none",
                self._batch_size,
            )
        except ImportError as e:
            LOGGER.warning(
                "OpenTelemetry SDK not available, spans will not be exported: %s", e
            )
            self._initialized = True  # Mark as initialized to avoid retrying

    def start(self) -> None:
        """Initialize the OTel SDK (BatchSpanProcessor starts automatically)."""
        self._ensure_initialized()
        if self._tracer is not None:
            LOGGER.info("OTel exporter started with BatchSpanProcessor")

    def export_span(self, span_data: SpanData) -> None:
        """Export a SpanData to the OTLP endpoint.

        Creates an OTel span which is automatically queued by BatchSpanProcessor
        and exported in batches.

        Args:
            span_data: The span to export
        """
        if self._tracer is None:
            return

        try:
            with self._tracer.start_as_current_span(
                span_data.operation_name,
                kind=OTelSpanExporter._SpanKind.INTERNAL,
            ) as span:
                for key, value in span_data.tags.items():
                    if value is not None:
                        if isinstance(value, (str, int, float, bool)):
                            span.set_attribute(key, value)
                        else:
                            span.set_attribute(key, str(value))
                span.set_status(
                    OTelSpanExporter._Status(OTelSpanExporter._StatusCode.OK)
                )
            OTelSpanExporter._export_count += 1

            if OTelSpanExporter._export_count % 100 == 0:
                LOGGER.info(
                    "OTel export progress: %d spans (%d errors)",
                    OTelSpanExporter._export_count,
                    OTelSpanExporter._export_error_count,
                )
        except (ValueError, TypeError, AttributeError) as e:
            OTelSpanExporter._export_error_count += 1
            if OTelSpanExporter._export_error_count <= 5:
                LOGGER.warning("Failed to export span: %s", e)

    def export_registration_span(
        self,
        trial_id: str,
        actor_id: str,
        actor_type: str,
        metadata: dict[str, Any],
    ) -> None:
        """Export an actor registration span.

        Args:
            trial_id: Trial identifier
            actor_id: Actor identifier
            actor_type: "agent" or "datastream"
            metadata: Actor metadata (name, model, tools, etc.)
        """
        span = convert_actor_registration_to_span(
            trial_id, actor_id, actor_type, metadata
        )
        self.export_span(span)

    def export_event_span(
        self,
        trial_id: str,
        actor_id: str,
        operation_name: str,
        tags: dict[str, Any] | None = None,
    ) -> None:
        """Export an event span.

        Args:
            trial_id: Trial identifier
            actor_id: Actor identifier
            operation_name: Operation/event type name
            tags: Additional tags for the span
        """
        extra_tags = tags or {}
        span = create_span_from_event(
            trial_id=trial_id,
            actor_id=actor_id,
            operation_name=operation_name,
            extra_tags=extra_tags,
        )
        self.export_span(span)

    def shutdown(self) -> None:
        """Flush remaining spans and shutdown the OTel provider."""
        if self._provider is not None:
            try:
                self._provider.shutdown()
            except (RuntimeError, OSError, TimeoutError) as e:
                LOGGER.warning("Error during OTel provider shutdown: %s", e)

        LOGGER.info(
            "OTel exporter shutdown: %d spans exported, %d errors",
            OTelSpanExporter._export_count,
            OTelSpanExporter._export_error_count,
        )


# Global exporter instances (lazily initialized)
_global_exporter: OTelSpanExporter | None = None


def get_otel_exporter() -> OTelSpanExporter | None:
    """Get the global OTel exporter instance."""
    return _global_exporter


def set_otel_exporter(exporter: OTelSpanExporter | None) -> None:
    """Set the global OTel exporter instance."""
    global _global_exporter
    _global_exporter = exporter


def emit_span(span_data: SpanData) -> None:
    """Emit a span using the configured OTLP exporter (e.g. Jaeger)."""
    if _global_exporter is not None:
        _global_exporter.export_span(span_data)


__all__ = [
    "JaegerTraceReader",
    "OTelSpanExporter",
    "SpanData",
    "TraceReader",
    "convert_actor_registration_to_span",
    "convert_agent_message_to_span",
    "convert_checkpoint_event_to_span",
    "create_span_from_event",
    "create_trace_reader",
    "emit_span",
    "get_otel_exporter",
    "load_spans_from_checkpoint",
    "set_otel_exporter",
]
