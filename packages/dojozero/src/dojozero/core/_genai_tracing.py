"""OpenTelemetry GenAI span emission for LLM calls.

Provides ``TracingChatModel`` — a generic wrapper around AgentScope's
``ChatModelBase`` that records one span per model call, following the
OpenTelemetry GenAI semantic conventions
(https://opentelemetry.io/docs/specs/semconv/gen-ai/).

The wrapper is opted in via ``wrap_model_for_tracing()``. Content capture,
per-message truncation, and the per-span content budget are controlled by
``DOJOZERO_TRACE_GENAI*`` env vars; see ``docs/tracing.md``.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import time as _time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agentscope.model import ChatModelBase

from dojozero.core._tracing import SpanData, emit_span

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings (env-driven; no central Pydantic Settings model exists today)
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def genai_tracing_enabled() -> bool:
    """Whether chat spans are emitted at all (master switch)."""
    return _env_bool("DOJOZERO_TRACE_GENAI", True)


def genai_capture_content() -> bool:
    """Whether message/tool content is captured on spans.

    The OTel standard env var takes precedence when set.
    """
    otel = os.getenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT")
    if otel is not None:
        return otel.strip().lower() in ("1", "true", "yes", "on")
    return _env_bool("DOJOZERO_TRACE_GENAI_CONTENT", True)


def genai_max_chars_per_message() -> int:
    return _env_int("DOJOZERO_TRACE_GENAI_CONTENT_MAX_CHARS", 262144)


def genai_max_chars_per_span() -> int:
    return _env_int("DOJOZERO_TRACE_GENAI_SPAN_MAX_CHARS", 4194304)


def genai_include_tools() -> bool:
    return _env_bool("DOJOZERO_TRACE_GENAI_INCLUDE_TOOLS", True)


# ---------------------------------------------------------------------------
# Parent-span context (set by BettingAgent around each ReActAgent turn)
# ---------------------------------------------------------------------------


# Stores the parent span id (the agent.response span) so chat spans emitted
# from anywhere inside the ReActAgent call become its children.
_current_parent_span_id: ContextVar[str | None] = ContextVar(
    "dojozero_current_parent_span_id", default=None
)


def set_parent_span_id(span_id: str | None) -> Any:
    """Set the current parent span id; returns a token to reset with."""
    return _current_parent_span_id.set(span_id)


def reset_parent_span_id(token: Any) -> None:
    _current_parent_span_id.reset(token)


def get_parent_span_id() -> str | None:
    return _current_parent_span_id.get()


# ---------------------------------------------------------------------------
# Event helper (stored on SpanData.logs in Jaeger-style shape)
# ---------------------------------------------------------------------------


def make_span_event(
    name: str,
    attributes: dict[str, Any] | None = None,
    timestamp_us: int | None = None,
) -> dict[str, Any]:
    """Build a Jaeger-style log/event dict consumable by SpanData.logs.

    Shape: ``{"timestamp": <us>, "fields": [{"key": "event", "value": <name>}, ...]}``.
    The OTLP exporter translates these to ``span.add_event`` calls; the SLS
    exporter serializes them as a JSON field.
    """
    if timestamp_us is None:
        timestamp_us = int(_time.time() * 1_000_000)
    fields: list[dict[str, Any]] = [{"key": "event", "value": name}]
    if attributes:
        for k, v in attributes.items():
            fields.append({"key": k, "value": v})
    return {"timestamp": timestamp_us, "fields": fields}


# ---------------------------------------------------------------------------
# Content extraction + truncation
# ---------------------------------------------------------------------------


def _truncate(value: Any, limit: int) -> tuple[Any, bool, int]:
    """Return ``(value_or_truncated, truncated, original_length)``.

    ``value`` is JSON-serialized if it isn't already a string so we can measure
    characters consistently.
    """
    if isinstance(value, str):
        serialized = value
    else:
        try:
            serialized = json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            serialized = str(value)
    original_length = len(serialized)
    if original_length <= limit:
        return value, False, original_length
    return serialized[:limit], True, original_length


def _stringify_content(content: Any) -> str:
    """Flatten AgentScope content blocks into a single string for span events.

    Content can be str, a list of blocks (dict w/ type=text|tool_use|...), or
    arbitrary. We produce a compact textual representation.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("type")
                if t == "text":
                    parts.append(str(item.get("text", "")))
                elif t == "thinking":
                    parts.append(str(item.get("thinking", "")))
                elif t == "tool_use":
                    parts.append(
                        json.dumps(
                            {
                                "type": "tool_use",
                                "id": item.get("id"),
                                "name": item.get("name"),
                                "input": item.get("input"),
                            },
                            default=str,
                            ensure_ascii=False,
                        )
                    )
                else:
                    parts.append(json.dumps(item, default=str, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    try:
        return json.dumps(content, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _extract_tool_calls(content: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(content, (list, tuple)):
        return out
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            out.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "arguments": item.get("input"),
                }
            )
    return out


def _provider_system_name(inner: ChatModelBase) -> str:
    """Map a concrete model class name to a ``gen_ai.system`` string."""
    cls_name = type(inner).__name__.lower()
    if "dashscope" in cls_name:
        return "dashscope"
    if "openai" in cls_name:
        return "openai"
    if "anthropic" in cls_name:
        return "anthropic"
    if "gemini" in cls_name or "google" in cls_name:
        return "gemini"
    if "ollama" in cls_name:
        return "ollama"
    return cls_name.replace("chatmodel", "") or "unknown"


# ---------------------------------------------------------------------------
# Core span builder
# ---------------------------------------------------------------------------


def _build_chat_span(
    *,
    trial_id: str,
    actor_id: str,
    parent_span_id: str | None,
    gen_ai_system: str,
    request_model: str | None,
    response_model: str | None,
    request_params: dict[str, Any],
    messages: list[dict[str, Any]] | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: Any,
    response_content: Any,
    response_id: str | None,
    usage: dict[str, Any] | None,
    finish_reasons: list[str] | None,
    error: BaseException | None,
    start_time: datetime,
    duration_us: int,
) -> SpanData:
    """Assemble a GenAI ``chat`` SpanData with attributes and message events."""
    capture = genai_capture_content()
    per_msg_limit = genai_max_chars_per_message()
    per_span_limit = genai_max_chars_per_span()

    tags: dict[str, Any] = {
        "actor.id": actor_id,
        "dojozero.trial.id": trial_id,
        "gen_ai.operation.name": "chat",
        "gen_ai.system": gen_ai_system,
    }
    if request_model:
        tags["gen_ai.request.model"] = request_model
    if response_model and response_model != request_model:
        tags["gen_ai.response.model"] = response_model
    if response_id:
        tags["gen_ai.response.id"] = response_id

    # Sampling params, surfaced as top-level attributes per semconv
    semconv_param_map = {
        "temperature": "gen_ai.request.temperature",
        "top_p": "gen_ai.request.top_p",
        "top_k": "gen_ai.request.top_k",
        "max_tokens": "gen_ai.request.max_tokens",
        "frequency_penalty": "gen_ai.request.frequency_penalty",
        "presence_penalty": "gen_ai.request.presence_penalty",
        "stop": "gen_ai.request.stop_sequences",
        "stop_sequences": "gen_ai.request.stop_sequences",
    }
    for key, attr in semconv_param_map.items():
        if key in request_params and request_params[key] is not None:
            tags[attr] = request_params[key]

    if usage:
        if "input_tokens" in usage:
            tags["gen_ai.usage.input_tokens"] = usage["input_tokens"]
        if "output_tokens" in usage:
            tags["gen_ai.usage.output_tokens"] = usage["output_tokens"]
        if "time" in usage:
            tags["gen_ai.usage.time_seconds"] = usage["time"]

    if finish_reasons:
        tags["gen_ai.response.finish_reasons"] = ",".join(finish_reasons)

    if error is not None:
        tags["error"] = True
        tags["error.type"] = type(error).__name__
        tags["error.message"] = str(error)[:512]

    if capture and genai_include_tools() and tools:
        try:
            tools_json = json.dumps(tools, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            tools_json = str(tools)
        truncated_tools, truncated_flag, orig_len = _truncate(tools_json, per_msg_limit)
        tags["gen_ai.request.tools"] = truncated_tools
        if truncated_flag:
            tags["gen_ai.request.tools.truncated"] = True
            tags["gen_ai.request.tools.original_length"] = orig_len

    if tool_choice is not None and not isinstance(tool_choice, (str, int, float, bool)):
        tags["gen_ai.request.tool_choice"] = str(tool_choice)
    elif tool_choice is not None:
        tags["gen_ai.request.tool_choice"] = tool_choice

    # Build span events (messages). Respects per-span character budget.
    start_us = int(start_time.timestamp() * 1_000_000)
    logs: list[dict[str, Any]] = []
    used_chars = 0
    dropped_messages = 0

    def _push_event(name: str, attrs: dict[str, Any]) -> bool:
        nonlocal used_chars
        if not capture:
            # Emit event shell without content so observers can still see message shape.
            attrs = {k: v for k, v in attrs.items() if k in ("role", "name", "id")}
        serialized_len = len(json.dumps(attrs, default=str, ensure_ascii=False))
        if used_chars + serialized_len > per_span_limit:
            return False
        used_chars += serialized_len
        logs.append(make_span_event(name, attrs, timestamp_us=start_us))
        return True

    if messages:
        # Drop oldest non-system messages first if over budget.
        budgeted_messages: list[dict[str, Any]] = []
        # First pass: always keep system messages.
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        # Keep most recent non-system messages up to budget.
        budgeted_messages.extend(system_msgs)
        # Reserve: approximate 80% of budget for messages, 20% for completion.
        msg_budget = int(per_span_limit * 0.8)
        running = sum(
            len(json.dumps(m, default=str, ensure_ascii=False)) for m in system_msgs
        )
        for msg in reversed(non_system):
            msg_len = len(json.dumps(msg, default=str, ensure_ascii=False))
            if running + msg_len > msg_budget:
                dropped_messages += 1
                continue
            running += msg_len
            budgeted_messages.append(msg)
        # Re-order: systems first, then non-system in original order
        seen_ids = {id(m) for m in budgeted_messages}
        ordered = system_msgs + [m for m in non_system if id(m) in seen_ids]

        for msg in ordered:
            role = str(msg.get("role", ""))
            content = msg.get("content")
            event_name = {
                "system": "gen_ai.system.message",
                "user": "gen_ai.user.message",
                "assistant": "gen_ai.assistant.message",
                "tool": "gen_ai.tool.message",
            }.get(role, "gen_ai.message")
            content_str = _stringify_content(content)
            content_trunc, trunc_flag, orig_len = _truncate(content_str, per_msg_limit)
            attrs: dict[str, Any] = {"role": role, "content": content_trunc}
            if trunc_flag:
                attrs["gen_ai.truncated"] = True
                attrs["gen_ai.original_length"] = orig_len
            name = msg.get("name")
            if name:
                attrs["name"] = name
            tool_calls = _extract_tool_calls(content) or msg.get("tool_calls")
            if tool_calls:
                attrs["tool_calls"] = tool_calls
            tool_call_id = msg.get("tool_call_id") or msg.get("id")
            if role == "tool" and tool_call_id:
                attrs["id"] = tool_call_id
            _push_event(event_name, attrs)

    # Response as gen_ai.choice event
    if response_content is not None or error is None:
        choice_attrs: dict[str, Any] = {"index": 0}
        if finish_reasons:
            choice_attrs["finish_reason"] = finish_reasons[0]
        tool_calls = _extract_tool_calls(response_content)
        content_str = _stringify_content(response_content)
        content_trunc, trunc_flag, orig_len = _truncate(content_str, per_msg_limit)
        msg_payload: dict[str, Any] = {"role": "assistant", "content": content_trunc}
        if trunc_flag:
            msg_payload["gen_ai.truncated"] = True
            msg_payload["gen_ai.original_length"] = orig_len
        if tool_calls:
            msg_payload["tool_calls"] = tool_calls
        choice_attrs["message"] = msg_payload
        _push_event("gen_ai.choice", choice_attrs)

    if dropped_messages:
        tags["gen_ai.truncated"] = True
        tags["gen_ai.truncated.dropped_messages"] = dropped_messages

    span_id = uuid4().hex[:16]
    return SpanData(
        trace_id=trial_id,
        span_id=span_id,
        operation_name="chat",
        start_time=start_us,
        duration=duration_us,
        parent_span_id=parent_span_id,
        tags=tags,
        logs=logs,
    )


# ---------------------------------------------------------------------------
# TracingChatModel wrapper
# ---------------------------------------------------------------------------


class TracingChatModel(ChatModelBase):
    """Wrap a ``ChatModelBase`` and emit a ``chat`` span per call.

    Non-streaming only. If the wrapped model is configured for streaming, we
    pass the async generator through unchanged and emit a degraded span.
    Eliminating streaming use is tracked as a separate issue.
    """

    def __init__(
        self,
        inner: ChatModelBase,
        trial_id: str,
        actor_id: str,
    ) -> None:
        # Don't call super().__init__ — that would re-initialize model_name / stream
        # with different semantics. Mirror the inner model's public attributes.
        # Use getattr with defaults so test doubles / mocks without the annotated
        # attributes still work.
        self._inner = inner
        self.model_name = getattr(inner, "model_name", "unknown")
        self.stream = getattr(inner, "stream", False)
        self._trial_id = trial_id
        self._actor_id = actor_id

    # Transparent attribute forwarding for anything AgentScope introspects on
    # the wrapped model.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not genai_tracing_enabled():
            return await self._inner(*args, **kwargs)

        # Bind against the concrete inner __call__ signature so we can pull
        # messages/tools/etc. without assuming positional order.
        try:
            sig = inspect.signature(self._inner.__call__)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = dict(bound.arguments)
        except (TypeError, ValueError):
            arguments = {}

        messages = arguments.get("messages")
        if not isinstance(messages, list):
            messages = None
        tools = arguments.get("tools")
        tool_choice = arguments.get("tool_choice")
        extra_kwargs = arguments.get("kwargs") or {}
        if not isinstance(extra_kwargs, dict):
            extra_kwargs = {}

        start_time = datetime.now(timezone.utc)
        start_mono = _time.monotonic()
        parent_span_id = get_parent_span_id()
        gen_ai_system = _provider_system_name(self._inner)

        try:
            result = await self._inner(*args, **kwargs)
        except BaseException as exc:
            duration_us = int((_time.monotonic() - start_mono) * 1_000_000)
            try:
                span = _build_chat_span(
                    trial_id=self._trial_id,
                    actor_id=self._actor_id,
                    parent_span_id=parent_span_id,
                    gen_ai_system=gen_ai_system,
                    request_model=self.model_name,
                    response_model=None,
                    request_params=extra_kwargs,
                    messages=messages,
                    tools=tools if isinstance(tools, list) else None,
                    tool_choice=tool_choice,
                    response_content=None,
                    response_id=None,
                    usage=None,
                    finish_reasons=None,
                    error=exc,
                    start_time=start_time,
                    duration_us=duration_us,
                )
                emit_span(span)
            except Exception:
                logger.exception("Failed to emit chat span on error path")
            raise

        duration_us = int((_time.monotonic() - start_mono) * 1_000_000)

        # If streaming, pass through the async generator unchanged and emit a
        # degraded span (no response content, no usage).
        from agentscope.model._model_response import ChatResponse

        if not isinstance(result, ChatResponse):
            try:
                span = _build_chat_span(
                    trial_id=self._trial_id,
                    actor_id=self._actor_id,
                    parent_span_id=parent_span_id,
                    gen_ai_system=gen_ai_system,
                    request_model=self.model_name,
                    response_model=None,
                    request_params=extra_kwargs,
                    messages=messages,
                    tools=tools if isinstance(tools, list) else None,
                    tool_choice=tool_choice,
                    response_content=None,
                    response_id=None,
                    usage=None,
                    finish_reasons=["streaming_unsupported"],
                    error=None,
                    start_time=start_time,
                    duration_us=duration_us,
                )
                span.tags["gen_ai.streaming"] = True
                emit_span(span)
            except Exception:
                logger.exception("Failed to emit chat span for streaming response")
            return result

        usage_dict: dict[str, Any] | None = None
        if result.usage is not None:
            usage_dict = {
                "input_tokens": getattr(result.usage, "input_tokens", None),
                "output_tokens": getattr(result.usage, "output_tokens", None),
                "time": getattr(result.usage, "time", None),
            }
            usage_dict = {k: v for k, v in usage_dict.items() if v is not None}

        finish_reasons: list[str] | None = None
        metadata = getattr(result, "metadata", None) or {}
        if isinstance(metadata, dict):
            fr = metadata.get("finish_reason") or metadata.get("stop_reason")
            if fr:
                finish_reasons = [str(fr)]

        try:
            span = _build_chat_span(
                trial_id=self._trial_id,
                actor_id=self._actor_id,
                parent_span_id=parent_span_id,
                gen_ai_system=gen_ai_system,
                request_model=self.model_name,
                response_model=metadata.get("model")
                if isinstance(metadata, dict)
                else None,
                request_params=extra_kwargs,
                messages=messages,
                tools=tools if isinstance(tools, list) else None,
                tool_choice=tool_choice,
                response_content=result.content,
                response_id=getattr(result, "id", None),
                usage=usage_dict,
                finish_reasons=finish_reasons,
                error=None,
                start_time=start_time,
                duration_us=duration_us,
            )
            emit_span(span)
        except Exception:
            logger.exception("Failed to emit chat span for successful response")

        return result


def wrap_model_for_tracing(
    model: ChatModelBase,
    trial_id: str,
    actor_id: str,
) -> ChatModelBase:
    """Wrap ``model`` so each invocation emits a GenAI ``chat`` span.

    Returns the input model unchanged if GenAI tracing is disabled at
    import time. Safe to call on an already-wrapped model (returns as-is).
    """
    if not genai_tracing_enabled():
        return model
    if isinstance(model, TracingChatModel):
        return model
    return TracingChatModel(model, trial_id=trial_id, actor_id=actor_id)


__all__ = [
    "TracingChatModel",
    "get_parent_span_id",
    "genai_capture_content",
    "genai_include_tools",
    "genai_max_chars_per_message",
    "genai_max_chars_per_span",
    "genai_tracing_enabled",
    "make_span_event",
    "reset_parent_span_id",
    "set_parent_span_id",
    "wrap_model_for_tracing",
]
