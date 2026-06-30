from __future__ import annotations

from dojozero.arena_server._utils import _detect_contest_kind
from dojozero.core._tracing import SpanData


def _span(op: str, tags: dict[str, object]) -> SpanData:
    return SpanData(
        trace_id="t",
        span_id=f"s-{op}",
        operation_name=op,
        start_time=1,
        duration=0,
        tags=tags,
    )


def _final_stats(contest_kind: str) -> SpanData:
    return _span("broker.final_stats", {"broker.contest_kind": contest_kind})


def _registered(actor: str) -> SpanData:
    return _span("operator.registered", {"actor.id": actor})


def test_final_stats_is_primary_signal() -> None:
    assert (
        _detect_contest_kind([_final_stats("window_pool_prediction")]) == "prediction"
    )
    assert _detect_contest_kind([_final_stats("classic_betting")]) == "betting"


def test_falls_back_to_operator_registered_for_in_progress_trials() -> None:
    # No final_stats yet (trial still running) — use the broker registration.
    assert _detect_contest_kind([_registered("prediction_broker")]) == "prediction"
    assert _detect_contest_kind([_registered("betting_broker")]) == "betting"


def test_final_stats_wins_over_registered() -> None:
    spans = [
        _registered("betting_broker"),
        _final_stats("window_pool_prediction"),
    ]
    assert _detect_contest_kind(spans) == "prediction"


def test_empty_when_no_broker_signal() -> None:
    assert _detect_contest_kind([_span("agent.response", {})]) == ""
    assert _detect_contest_kind([]) == ""
