"""Wrap an agentscope ``ChatModelBase`` to report ``model.call`` events.

Each LLM invocation by the ReActAgent triggers one report back to the
gateway, which forwards it to aip-activity as a Tier-1 ``model.call``.
We capture token counts + latency from the response's ``usage`` field
and outcome from whether the call raised.

Streaming mode is **not** instrumented in the canary: the runner sets
``stream=False`` everywhere. If a deployment flips streaming on, calls
pass through unwrapped (logged once at construction).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, AsyncGenerator

from agentscope.model import ChatModelBase
from agentscope.model._model_response import ChatResponse

if TYPE_CHECKING:
    from dojozero_client import TrialConnection

logger = logging.getLogger(__name__)


class EmittingChatModel(ChatModelBase):
    """Pass-through wrapper that reports model.call after each invocation.

    Construction args:
        wrapped: any ``ChatModelBase`` (the real model).
        connection: the runner's ``TrialConnection``; we call
            ``report_model_call`` on it after each invocation.

    Behaviour:
        - Non-streaming (``wrapped.stream is False``): await the wrapped
          model, extract usage from the ``ChatResponse``, report.
        - Streaming: forwards untouched and logs once. Adding streaming
          instrumentation is a follow-up; not needed for the canary.
    """

    def __init__(
        self,
        wrapped: ChatModelBase,
        connection: "TrialConnection",
    ) -> None:
        super().__init__(model_name=wrapped.model_name, stream=wrapped.stream)
        self._wrapped = wrapped
        self._connection = connection
        if wrapped.stream:
            logger.info(
                "EmittingChatModel: %s is streaming; model.call reporting "
                "skipped (canary uses stream=False)",
                wrapped.model_name,
            )

    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        if self._wrapped.stream:
            return await self._wrapped(*args, **kwargs)

        started = time.monotonic()
        try:
            response = await self._wrapped(*args, **kwargs)
        except Exception:
            duration_ms = int((time.monotonic() - started) * 1000)
            await self._report(
                tokens_in=0,
                tokens_out=0,
                latency_ms=duration_ms,
                outcome="error",
            )
            raise

        duration_ms = int((time.monotonic() - started) * 1000)
        usage = (
            getattr(response, "usage", None)
            if isinstance(response, ChatResponse)
            else None
        )
        await self._report(
            tokens_in=int(getattr(usage, "input_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "output_tokens", 0) or 0),
            latency_ms=duration_ms,
            outcome="success",
        )
        return response

    async def _report(
        self,
        *,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        outcome: str,
    ) -> None:
        try:
            await self._connection.report_model_call(
                model=self._wrapped.model_name,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                outcome=outcome,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "EmittingChatModel: report_model_call failed (%s); "
                "model call itself was not affected",
                exc,
            )


__all__ = ["EmittingChatModel"]
