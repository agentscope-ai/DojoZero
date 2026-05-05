"""Tests for EmittingChatModel — the model.call instrumentation wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from agentscope.model import ChatModelBase
from agentscope.model._model_response import ChatResponse
from agentscope.model._model_usage import ChatUsage

from dojozero_agent_runner._emitting_model import EmittingChatModel


class _StubModel(ChatModelBase):
    """Minimal ChatModelBase that returns a configured ChatResponse."""

    def __init__(
        self,
        *,
        response: ChatResponse | None = None,
        raises: Exception | None = None,
        stream: bool = False,
    ) -> None:
        super().__init__(model_name="stub-model", stream=stream)
        self._response = response
        self._raises = raises

    async def __call__(self, *_args, **_kwargs):
        if self._raises is not None:
            raise self._raises
        return self._response


def _response_with_usage(input_tokens: int, output_tokens: int) -> ChatResponse:
    return ChatResponse(
        content=[],
        usage=ChatUsage(
            input_tokens=input_tokens, output_tokens=output_tokens, time=0.1
        ),
    )


@pytest.fixture
def connection():
    conn = MagicMock()
    conn.report_model_call = AsyncMock()
    return conn


class TestSuccessPath:
    @pytest.mark.asyncio
    async def test_reports_usage_after_success(self, connection):
        wrapped = _StubModel(response=_response_with_usage(120, 80))
        em = EmittingChatModel(wrapped, connection)
        result = await em()
        assert isinstance(result, ChatResponse)
        connection.report_model_call.assert_awaited_once()
        kwargs = connection.report_model_call.await_args.kwargs
        assert kwargs["model"] == "stub-model"
        assert kwargs["tokens_in"] == 120
        assert kwargs["tokens_out"] == 80
        assert kwargs["outcome"] == "success"
        assert kwargs["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_response_without_usage_reports_zeros(self, connection):
        wrapped = _StubModel(response=ChatResponse(content=[], usage=None))
        em = EmittingChatModel(wrapped, connection)
        await em()
        kwargs = connection.report_model_call.await_args.kwargs
        assert kwargs["tokens_in"] == 0
        assert kwargs["tokens_out"] == 0
        assert kwargs["outcome"] == "success"


class TestErrorPath:
    @pytest.mark.asyncio
    async def test_reports_error_outcome_and_reraises(self, connection):
        wrapped = _StubModel(raises=RuntimeError("LLM exploded"))
        em = EmittingChatModel(wrapped, connection)
        with pytest.raises(RuntimeError, match="exploded"):
            await em()
        connection.report_model_call.assert_awaited_once()
        kwargs = connection.report_model_call.await_args.kwargs
        assert kwargs["outcome"] == "error"
        # No usage available on error path.
        assert kwargs["tokens_in"] == 0
        assert kwargs["tokens_out"] == 0


class TestReportFailureSwallowed:
    @pytest.mark.asyncio
    async def test_report_failure_does_not_break_call(self, connection, caplog):
        connection.report_model_call.side_effect = RuntimeError("gateway 503")
        wrapped = _StubModel(response=_response_with_usage(1, 1))
        em = EmittingChatModel(wrapped, connection)
        with caplog.at_level("WARNING"):
            result = await em()
        # The model call itself succeeded.
        assert isinstance(result, ChatResponse)
        assert any("report_model_call failed" in r.message for r in caplog.records)


class TestStreamingPassthrough:
    @pytest.mark.asyncio
    async def test_streaming_skips_reporting(self, connection, caplog):
        wrapped = _StubModel(response=_response_with_usage(50, 25), stream=True)
        with caplog.at_level("INFO"):
            em = EmittingChatModel(wrapped, connection)
        # Construction logs that streaming is uninstrumented.
        assert any("streaming" in r.message for r in caplog.records)
        await em()
        connection.report_model_call.assert_not_awaited()
