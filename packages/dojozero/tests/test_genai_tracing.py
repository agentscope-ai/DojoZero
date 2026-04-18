"""Tests for LLM/GenAI tracing integration.

LLM-level span emission is delegated to AgentScope's built-in ``@trace_llm``
decorator (see ``agentscope.tracing``). These tests cover the dojozero-side
integration points:

- ``enable_agentscope_tracing`` flips AgentScope's trace flag and optionally
  sets ``run_id``.
- ``emit_span_sls_only`` forwards to the SLS Log exporter without going
  through the OTLP path again (used by ``BettingAgent.agent.response``).
- The SLS Log exporter serializes ``SpanData.logs`` as a JSON ``_events``
  field.
- The arena read-path whitelist (``ARENA_RENDERED_OPERATIONS``) covers every
  operation in the ``deserialize_span`` dispatch + every ``EventTypes`` value
  and excludes the AgentScope-emitted ``chat`` spans.
"""

from __future__ import annotations

import json
from typing import Any

from dojozero.core._tracing import (
    SpanData,
    emit_span_sls_only,
    enable_agentscope_tracing,
)


# ---------------------------------------------------------------------------
# AgentScope integration
# ---------------------------------------------------------------------------


class TestEnableAgentScopeTracing:
    def test_flips_trace_enabled(self) -> None:
        from agentscope import _config as as_config

        as_config.trace_enabled = False
        assert enable_agentscope_tracing() is True
        assert as_config.trace_enabled is True

    def test_sets_run_id_when_given(self) -> None:
        from agentscope import _config as as_config

        enable_agentscope_tracing(run_id="trial-xyz")
        assert as_config.run_id == "trial-xyz"


# ---------------------------------------------------------------------------
# emit_span_sls_only — bypasses OTLP; forwards to SLS only when configured
# ---------------------------------------------------------------------------


class TestEmitSpanSlsOnly:
    def test_no_op_when_no_exporter_configured(self, monkeypatch) -> None:
        from dojozero.core import _tracing as t

        monkeypatch.setattr(t, "_global_sls_log_exporter", None)
        # Should simply not raise.
        emit_span_sls_only(
            SpanData(
                trace_id="t",
                span_id="s",
                operation_name="agent.response",
                start_time=0,
                duration=0,
            )
        )

    def test_forwards_to_sls_when_configured(self, monkeypatch) -> None:
        from dojozero.core import _tracing as t

        captured: list[SpanData] = []

        class _FakeSlsExporter:
            def export_span(self, span: SpanData) -> None:
                captured.append(span)

        monkeypatch.setattr(t, "_global_sls_log_exporter", _FakeSlsExporter())
        # Also ensure the OTLP exporter would not be called.
        otlp_calls: list[Any] = []

        class _FakeOtel:
            def export_span(self, span: SpanData) -> None:
                otlp_calls.append(span)

        monkeypatch.setattr(t, "_global_exporter", _FakeOtel())

        span = SpanData(
            trace_id="t",
            span_id="s",
            operation_name="agent.response",
            start_time=0,
            duration=0,
        )
        emit_span_sls_only(span)

        assert captured == [span]
        assert otlp_calls == []  # OTLP path must not be called


# ---------------------------------------------------------------------------
# Span-events serialization (SLS _events field)
# ---------------------------------------------------------------------------


class TestSpanEventsExport:
    def test_sls_exporter_serializes_events_to_field(self) -> None:
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
            operation_name="agent.response",
            start_time=1_700_000_000_000_000,
            duration=1000,
            tags={"actor.id": "a"},
            logs=[
                {
                    "timestamp": 1_700_000_000_000_000,
                    "fields": [
                        {"key": "event", "value": "example"},
                        {"key": "note", "value": "hi"},
                    ],
                }
            ],
        )
        exporter.export_span(span)
        assert len(captured) == 1
        entry = captured[0]
        assert "_events" in entry
        decoded = json.loads(entry["_events"])
        assert decoded[0]["fields"][0] == {"key": "event", "value": "example"}


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

        # AgentScope's @trace_llm emits spans named ``chat {model}``; none of
        # those should be in our whitelist (they'd never be a literal match
        # anyway, but we also don't include the prefix on its own).
        assert "chat" not in ARENA_RENDERED_OPERATIONS
        for op in ARENA_RENDERED_OPERATIONS:
            assert not op.startswith("chat "), f"unexpected: {op}"
