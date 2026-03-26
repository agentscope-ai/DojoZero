"""Arena span bucketing: semantic trial id vs Jaeger traceID."""

from typing import Any, cast

from dojozero.arena_server._utils import trial_id_for_span_grouping
from dojozero.core._tracing import SpanData


def _span(**kwargs: Any) -> SpanData:
    base: dict[str, Any] = dict(
        trace_id="a1b2c3d4e5f6789012345678abcdef01",
        span_id="span1",
        operation_name="event.nba_game_update",
        start_time=1,
        duration=0,
        tags={},
        logs=[],
    )
    base.update(kwargs)
    return SpanData(
        trace_id=cast(str, base["trace_id"]),
        span_id=cast(str, base["span_id"]),
        operation_name=cast(str, base["operation_name"]),
        start_time=cast(int, base["start_time"]),
        duration=cast(int, base["duration"]),
        parent_span_id=cast(str | None, base.get("parent_span_id")),
        tags=cast(dict[str, Any], base["tags"]),
        logs=cast(list[dict[str, Any]], base["logs"]),
    )


def test_trial_id_for_span_grouping_prefers_dojozero_tag():
    s = _span(
        tags={"dojozero.trial.id": "nba-game-401810868-214f899f"},
    )
    assert trial_id_for_span_grouping(s) == "nba-game-401810868-214f899f"


def test_trial_id_for_span_grouping_falls_back_to_trace_id():
    s = _span(trace_id="onlyjaegertraceid", tags={})
    assert trial_id_for_span_grouping(s) == "onlyjaegertraceid"


def test_create_span_from_event_sets_trial_tag():
    from dojozero.core._tracing import create_span_from_event

    span = create_span_from_event(
        trial_id="t-1",
        actor_id="hub",
        operation_name="event.x",
        extra_tags={"foo": "bar"},
    )
    assert span.tags.get("dojozero.trial.id") == "t-1"
    assert span.tags.get("foo") == "bar"
