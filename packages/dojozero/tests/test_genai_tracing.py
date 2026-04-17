"""Tests for GenAI OTel tracing (``core/_genai_tracing.py``).

Covers the ``TracingChatModel`` wrapper, message/content truncation, content
capture flag, error paths, span-events export, and the arena projection
whitelist.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from agentscope.message import TextBlock, ToolUseBlock
from agentscope.model import ChatModelBase
from agentscope.model._model_response import ChatResponse
from agentscope.model._model_usage import ChatUsage

from dojozero.core._genai_tracing import (
    TracingChatModel,
    genai_capture_content,
    genai_max_chars_per_message,
    reset_parent_span_id,
    set_parent_span_id,
    wrap_model_for_tracing,
)
from dojozero.core._tracing import SpanData


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakeModel(ChatModelBase):
    """Minimal ChatModelBase stand-in; returns whatever response is preloaded."""

    def __init__(
        self,
        response: ChatResponse | None = None,
        error: Exception | None = None,
        *,
        model_name: str = "fake-model",
        stream: bool = False,
    ) -> None:
        self.model_name = model_name
        self.stream = stream
        self._response = response
        self._error = error
        self.last_call: dict[str, Any] | None = None

    async def __call__(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        structured_model: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.last_call = {
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "kwargs": kwargs,
        }
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _make_response(
    *,
    text: str = "hello",
    input_tokens: int = 10,
    output_tokens: int = 5,
    tool_calls: list[dict[str, Any]] | None = None,
) -> ChatResponse:
    content: list[TextBlock | ToolUseBlock] = [TextBlock(type="text", text=text)]
    if tool_calls:
        for tc in tool_calls:
            content.append(
                ToolUseBlock(
                    type="tool_use",
                    id=tc.get("id", ""),
                    name=tc.get("name", ""),
                    input=tc.get("input", {}),
                )
            )
    return ChatResponse(
        content=content,
        usage=ChatUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            time=0.01,
        ),
        metadata={"finish_reason": "stop"},
    )


# ---------------------------------------------------------------------------
# Core wrapper tests
# ---------------------------------------------------------------------------


class TestTracingChatModel:
    @pytest.mark.asyncio
    async def test_emits_chat_span_on_success(self) -> None:
        inner = _FakeModel(response=_make_response(text="hi there"))
        wrapped = TracingChatModel(inner, trial_id="trial-1", actor_id="actor-A")

        captured: list[SpanData] = []
        with patch(
            "dojozero.core._genai_tracing.emit_span",
            side_effect=captured.append,
        ):
            await wrapped(
                messages=[
                    {"role": "system", "content": "you are a bot"},
                    {"role": "user", "content": "hello"},
                ],
                temperature=0.7,
                max_tokens=128,
            )

        assert len(captured) == 1
        span = captured[0]
        assert span.operation_name == "chat"
        assert span.trace_id == "trial-1"
        assert span.tags["actor.id"] == "actor-A"
        assert span.tags["dojozero.trial.id"] == "trial-1"
        assert span.tags["gen_ai.operation.name"] == "chat"
        assert span.tags["gen_ai.request.model"] == "fake-model"
        assert span.tags["gen_ai.usage.input_tokens"] == 10
        assert span.tags["gen_ai.usage.output_tokens"] == 5
        assert span.tags["gen_ai.request.temperature"] == 0.7
        assert span.tags["gen_ai.request.max_tokens"] == 128
        assert span.tags["gen_ai.response.finish_reasons"] == "stop"

        event_names = [
            f["value"]
            for log in span.logs
            for f in log.get("fields", [])
            if f.get("key") == "event"
        ]
        assert "gen_ai.system.message" in event_names
        assert "gen_ai.user.message" in event_names
        assert "gen_ai.choice" in event_names

    @pytest.mark.asyncio
    async def test_parent_span_id_propagates_from_contextvar(self) -> None:
        inner = _FakeModel(response=_make_response())
        wrapped = TracingChatModel(inner, trial_id="trial-1", actor_id="actor-A")

        captured: list[SpanData] = []
        token = set_parent_span_id("parent-abc")
        try:
            with patch(
                "dojozero.core._genai_tracing.emit_span",
                side_effect=captured.append,
            ):
                await wrapped(messages=[{"role": "user", "content": "hi"}])
        finally:
            reset_parent_span_id(token)

        assert captured[0].parent_span_id == "parent-abc"

    @pytest.mark.asyncio
    async def test_error_path_emits_span_and_reraises(self) -> None:
        inner = _FakeModel(error=RuntimeError("boom"))
        wrapped = TracingChatModel(inner, trial_id="trial-1", actor_id="actor-A")

        captured: list[SpanData] = []
        with patch(
            "dojozero.core._genai_tracing.emit_span",
            side_effect=captured.append,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await wrapped(messages=[{"role": "user", "content": "hi"}])

        assert len(captured) == 1
        span = captured[0]
        assert span.tags.get("error") is True
        assert span.tags.get("error.type") == "RuntimeError"
        assert "boom" in span.tags.get("error.message", "")

    @pytest.mark.asyncio
    async def test_content_flag_drops_content(self, monkeypatch) -> None:
        monkeypatch.setenv("DOJOZERO_TRACE_GENAI_CONTENT", "false")
        monkeypatch.delenv(
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
            raising=False,
        )
        assert genai_capture_content() is False

        inner = _FakeModel(response=_make_response(text="secret-reply"))
        wrapped = TracingChatModel(inner, trial_id="trial-1", actor_id="actor-A")

        captured: list[SpanData] = []
        with patch(
            "dojozero.core._genai_tracing.emit_span",
            side_effect=captured.append,
        ):
            await wrapped(
                messages=[{"role": "user", "content": "sensitive prompt"}],
                tools=[{"type": "function", "function": {"name": "x"}}],
            )

        span = captured[0]
        # Tools attribute omitted when content capture is off.
        assert "gen_ai.request.tools" not in span.tags
        # Message events still present as shells (role/name/id only) but no content.
        for log in span.logs:
            for field in log.get("fields", []):
                if field.get("key") == "content":
                    pytest.fail("content should not be emitted when flag is off")

    @pytest.mark.asyncio
    async def test_truncation_marks_original_length(self, monkeypatch) -> None:
        monkeypatch.setenv("DOJOZERO_TRACE_GENAI_CONTENT_MAX_CHARS", "50")
        assert genai_max_chars_per_message() == 50

        big = "x" * 5000
        inner = _FakeModel(response=_make_response(text=big))
        wrapped = TracingChatModel(inner, trial_id="trial-1", actor_id="actor-A")

        captured: list[SpanData] = []
        with patch(
            "dojozero.core._genai_tracing.emit_span",
            side_effect=captured.append,
        ):
            await wrapped(messages=[{"role": "user", "content": big}])

        span = captured[0]
        truncated_markers = 0
        for log in span.logs:
            fields = {f["key"]: f.get("value") for f in log.get("fields", [])}
            if fields.get("gen_ai.truncated") is True:
                truncated_markers += 1
                assert isinstance(fields.get("gen_ai.original_length"), int)
                assert fields["gen_ai.original_length"] >= 5000
        assert truncated_markers >= 1

    @pytest.mark.asyncio
    async def test_tracing_disabled_is_passthrough(self, monkeypatch) -> None:
        monkeypatch.setenv("DOJOZERO_TRACE_GENAI", "false")
        inner = _FakeModel(response=_make_response())
        wrapped = wrap_model_for_tracing(inner, trial_id="trial-1", actor_id="actor-A")
        # Master switch off → factory returns the bare inner model.
        assert wrapped is inner

    def test_factory_idempotent(self) -> None:
        inner = _FakeModel(response=_make_response())
        once = wrap_model_for_tracing(inner, trial_id="t", actor_id="a")
        twice = wrap_model_for_tracing(once, trial_id="t", actor_id="a")
        assert once is twice


# ---------------------------------------------------------------------------
# Span-events export path
# ---------------------------------------------------------------------------


class TestSpanEventsExport:
    def test_sls_exporter_serializes_events_to_field(self) -> None:
        # Avoid importing SLS SDK; patch the class and its queue
        from dojozero.core._genai_tracing import make_span_event
        from dojozero.core._tracing import SLSLogExporter

        exporter = SLSLogExporter.__new__(SLSLogExporter)  # skip SDK init
        exporter._service_name = "test"  # type: ignore[attr-defined]
        captured: list[dict[str, Any]] = []

        class _FakeQueue:
            def put_nowait(self, item: Any) -> None:
                captured.append(item)

        exporter._queue = _FakeQueue()  # type: ignore[attr-defined]

        span = SpanData(
            trace_id="trial-1",
            span_id="s1",
            operation_name="chat",
            start_time=1_700_000_000_000_000,
            duration=1000,
            tags={"gen_ai.system": "openai"},
            logs=[make_span_event("gen_ai.user.message", {"content": "hi"})],
        )
        exporter.export_span(span)
        assert len(captured) == 1
        entry = captured[0]
        assert "_events" in entry
        decoded = json.loads(entry["_events"])
        assert decoded[0]["fields"][0] == {
            "key": "event",
            "value": "gen_ai.user.message",
        }


# ---------------------------------------------------------------------------
# Arena projection whitelist
# ---------------------------------------------------------------------------


class TestArenaProjection:
    def test_whitelist_contains_rendered_operations(self) -> None:
        from dojozero.arena_server._utils import ARENA_RENDERED_OPERATIONS

        # Every operation name in the deserialize_span dispatch must be present.
        must_include = {
            "trial.started",
            "trial.stopped",
            "trial.terminated",
            "agent.response",
            "agent.agent_initialize",
            "broker.bet",
            "broker.state_update",
            "broker.bet_executed",
            "broker.final_stats",
        }
        for op in must_include:
            assert op in ARENA_RENDERED_OPERATIONS, f"missing {op}"

    def test_whitelist_covers_all_event_types(self) -> None:
        from dojozero.arena_server._utils import ARENA_RENDERED_OPERATIONS
        from dojozero.data import EventTypes

        for item in EventTypes:
            assert item.value in ARENA_RENDERED_OPERATIONS

    def test_whitelist_excludes_chat(self) -> None:
        from dojozero.arena_server._utils import ARENA_RENDERED_OPERATIONS

        assert "chat" not in ARENA_RENDERED_OPERATIONS
