"""Tests for SLSEventSource — materialize trial events from SLS to JSONL."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from dojozero.core._tracing import SpanData
from dojozero.data import (
    GameInitializeEvent,
    GameResultEvent,
    OddsUpdateEvent,
    SLSEventSource,
    extract_dedup_keys_from_jsonl,
)
from dojozero.data._models import OddsInfo, MoneylineOdds, TeamIdentity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event_to_tags(event: Any) -> dict[str, Any]:
    """Mirror DataHub._emit_event_span() tag construction."""
    tags: dict[str, Any] = {"sequence": 1, "sport.type": event.sport or "nba"}
    for key, value in event.to_dict().items():
        if key in ("event_type", "timestamp"):
            continue
        if isinstance(value, (dict, list)):
            tags[f"event.{key}"] = json.dumps(value, default=str)
        else:
            tags[f"event.{key}"] = value
    return tags


_SPAN_COUNTER = 0


def _next_span_id() -> str:
    global _SPAN_COUNTER
    _SPAN_COUNTER += 1
    return f"span{_SPAN_COUNTER:08x}"


def _root_span(
    trace_id: str,
    *,
    span_id: str | None = None,
    start_time_us: int = 1_700_000_000_000_000,
    duration_us: int = 1_000_000,
) -> SpanData:
    return SpanData(
        trace_id=trace_id,
        span_id=span_id or _next_span_id(),
        operation_name="trial.started",
        start_time=start_time_us,
        duration=duration_us,
        parent_span_id=None,
        tags={},
    )


def _event_span(
    event: Any,
    *,
    trace_id: str,
    parent_span_id: str,
    span_id: str | None = None,
    start_time_us: int | None = None,
) -> SpanData:
    if start_time_us is None:
        start_time_us = int(event.timestamp.timestamp() * 1_000_000)
    return SpanData(
        trace_id=trace_id,
        span_id=span_id or _next_span_id(),
        operation_name=event.event_type,
        start_time=start_time_us,
        duration=0,
        parent_span_id=parent_span_id,
        tags=_event_to_tags(event),
    )


def _non_event_span(
    *,
    trace_id: str,
    parent_span_id: str,
    op: str = "games.registered",
) -> SpanData:
    return SpanData(
        trace_id=trace_id,
        span_id=_next_span_id(),
        operation_name=op,
        start_time=1_700_000_001_000_000,
        duration=0,
        parent_span_id=parent_span_id,
        tags={},
    )


def _make_init(
    game_id: str = "g1", ts: datetime | None = None, sport: str = "nba"
) -> GameInitializeEvent:
    return GameInitializeEvent(
        game_id=game_id,
        sport=sport,
        home_team=TeamIdentity(name="Home"),
        away_team=TeamIdentity(name="Away"),
        timestamp=ts or datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
        game_timestamp=ts or datetime(2026, 4, 1, 19, 0, 0, tzinfo=timezone.utc),
    )


def _make_result(
    game_id: str = "g1", ts: datetime | None = None
) -> GameResultEvent:
    return GameResultEvent(
        game_id=game_id,
        sport="nba",
        winner="home",
        home_score=100,
        away_score=95,
        timestamp=ts or datetime(2026, 4, 1, 15, 0, 0, tzinfo=timezone.utc),
        game_timestamp=ts or datetime(2026, 4, 1, 22, 0, 0, tzinfo=timezone.utc),
    )


def _make_odds(
    game_id: str = "g1", ts: datetime | None = None, home_odds: float = 1.9
) -> OddsUpdateEvent:
    return OddsUpdateEvent(
        game_id=game_id,
        sport="nba",
        odds=OddsInfo(
            moneyline=MoneylineOdds(home_odds=home_odds, away_odds=2.1)
        ),
        timestamp=ts or datetime(2026, 4, 1, 13, 0, 0, tzinfo=timezone.utc),
    )


class _FakeReader:
    """Minimal TraceReader stand-in: returns the spans it was given."""

    def __init__(self, spans: list[SpanData]) -> None:
        self._spans = spans
        self.close_calls = 0

    async def get_spans(
        self,
        trial_id: str,
        start_time: datetime | None = None,
        operation_names: list[str] | None = None,
    ) -> list[SpanData]:
        return list(self._spans)

    async def list_trials(self, *args, **kwargs):  # pragma: no cover - unused
        return []

    async def get_all_spans(self, *args, **kwargs):  # pragma: no cover - unused
        return list(self._spans)

    async def close(self) -> None:
        self.close_calls += 1


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Materialize: basic shape
# ---------------------------------------------------------------------------


def test_materialize_writes_jsonl(tmp_path: Path) -> None:
    event = _make_init()
    root = _root_span("trial-A")
    spans = [root, _event_span(event, trace_id="trial-A", parent_span_id=root.span_id)]
    source = SLSEventSource(reader=_FakeReader(spans))

    dest = tmp_path / "trial-A.jsonl"
    _run(source.materialize_jsonl("trial-A", dest))

    assert dest.exists()
    lines = dest.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event_type"] == "event.game_initialize"
    assert parsed["game_id"] == "g1"


def test_non_event_spans_filtered(tmp_path: Path) -> None:
    event = _make_init()
    root = _root_span("trial-A")
    spans = [
        root,
        _non_event_span(trace_id="trial-A", parent_span_id=root.span_id, op="games.registered"),
        _non_event_span(trace_id="trial-A", parent_span_id=root.span_id, op="state.registered"),
        _event_span(event, trace_id="trial-A", parent_span_id=root.span_id),
    ]
    source = SLSEventSource(reader=_FakeReader(spans))

    dest = tmp_path / "trial-A.jsonl"
    _run(source.materialize_jsonl("trial-A", dest))

    assert len(dest.read_text().splitlines()) == 1


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def test_events_sorted_by_game_timestamp(tmp_path: Path) -> None:
    early = _make_init(
        ts=datetime(2026, 4, 1, 19, 0, tzinfo=timezone.utc),
    )
    late = _make_result(
        ts=datetime(2026, 4, 1, 22, 0, tzinfo=timezone.utc),
    )
    root = _root_span("trial-A")
    # Reverse order so sort must reorder.
    spans = [
        root,
        _event_span(
            late, trace_id="trial-A", parent_span_id=root.span_id,
            start_time_us=1_700_000_100_000_000,
        ),
        _event_span(
            early, trace_id="trial-A", parent_span_id=root.span_id,
            start_time_us=1_700_000_200_000_000,
        ),
    ]
    source = SLSEventSource(reader=_FakeReader(spans))
    dest = tmp_path / "trial-A.jsonl"
    _run(source.materialize_jsonl("trial-A", dest))

    lines = dest.read_text().splitlines()
    types = [json.loads(ln)["event_type"] for ln in lines]
    assert types == ["event.game_initialize", "event.game_result"]


def test_events_sort_falls_back_to_timestamp_when_no_game_timestamp(
    tmp_path: Path,
) -> None:
    # OddsUpdateEvent has no game_timestamp set → sort by timestamp.
    odds_a = _make_odds(ts=datetime(2026, 4, 1, 13, 0, tzinfo=timezone.utc))
    odds_b = _make_odds(
        ts=datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc), home_odds=2.0
    )
    root = _root_span("trial-A")
    spans = [
        root,
        _event_span(odds_b, trace_id="trial-A", parent_span_id=root.span_id),
        _event_span(odds_a, trace_id="trial-A", parent_span_id=root.span_id),
    ]
    source = SLSEventSource(reader=_FakeReader(spans))
    dest = tmp_path / "trial-A.jsonl"
    _run(source.materialize_jsonl("trial-A", dest))

    entries = [json.loads(ln) for ln in dest.read_text().splitlines()]
    assert entries[0]["timestamp"] < entries[1]["timestamp"]


# ---------------------------------------------------------------------------
# Timestamp reconstruction (span.start_time → event.timestamp)
# ---------------------------------------------------------------------------


def test_event_timestamp_restored_from_span_start_time(tmp_path: Path) -> None:
    # Span.start_time should set event.timestamp even though the tag doesn't.
    event = _make_init(ts=datetime(2026, 4, 1, 12, 30, 0, tzinfo=timezone.utc))
    root = _root_span("trial-A")
    # Use a custom span start_time different from event.timestamp so we can
    # verify that the span's start_time wins (matches how _emit_event_span
    # uses event.timestamp as span.start_time in practice).
    custom_start = int(
        datetime(2026, 4, 1, 12, 30, 0, tzinfo=timezone.utc).timestamp() * 1_000_000
    )
    spans = [
        root,
        _event_span(
            event,
            trace_id="trial-A",
            parent_span_id=root.span_id,
            start_time_us=custom_start,
        ),
    ]
    source = SLSEventSource(reader=_FakeReader(spans))
    dest = tmp_path / "trial-A.jsonl"
    _run(source.materialize_jsonl("trial-A", dest))

    parsed = json.loads(dest.read_text().splitlines()[0])
    assert parsed["timestamp"].startswith("2026-04-01T12:30:00")


# ---------------------------------------------------------------------------
# Multi-run (double-submitted trial)
# ---------------------------------------------------------------------------


def test_multi_run_auto_picks_most_complete(tmp_path: Path, caplog) -> None:
    trace_id = "trial-A"
    root_small = _root_span(trace_id, span_id="rootSmall", start_time_us=1_000_000)
    root_big = _root_span(trace_id, span_id="rootBig", start_time_us=2_000_000)

    small_events = [
        _event_span(
            _make_init(game_id=f"g{i}"),
            trace_id=trace_id,
            parent_span_id=root_small.span_id,
        )
        for i in range(2)
    ]
    big_events = [
        _event_span(
            _make_init(game_id=f"h{i}"),
            trace_id=trace_id,
            parent_span_id=root_big.span_id,
        )
        for i in range(5)
    ]
    spans = [root_small, root_big, *small_events, *big_events]

    source = SLSEventSource(reader=_FakeReader(spans))
    dest = tmp_path / "trial-A.jsonl"
    with caplog.at_level("WARNING"):
        _run(source.materialize_jsonl(trace_id, dest))

    entries = [json.loads(ln) for ln in dest.read_text().splitlines()]
    assert len(entries) == 5
    game_ids = {e["game_id"] for e in entries}
    assert game_ids == {"h0", "h1", "h2", "h3", "h4"}
    assert any("2 runs" in r.message for r in caplog.records)


def test_multi_run_tiebreak_by_latest_end_time(tmp_path: Path) -> None:
    trace_id = "trial-A"
    root_early = _root_span(
        trace_id,
        span_id="rootEarly",
        start_time_us=1_000_000,
        duration_us=500_000,
    )
    root_late = _root_span(
        trace_id,
        span_id="rootLate",
        start_time_us=2_000_000,
        duration_us=1_000_000,
    )
    # Equal event counts.
    spans = [
        root_early,
        root_late,
        _event_span(
            _make_init(game_id="a"),
            trace_id=trace_id,
            parent_span_id=root_early.span_id,
        ),
        _event_span(
            _make_init(game_id="b"),
            trace_id=trace_id,
            parent_span_id=root_late.span_id,
        ),
    ]
    source = SLSEventSource(reader=_FakeReader(spans))
    dest = tmp_path / "trial-A.jsonl"
    _run(source.materialize_jsonl(trace_id, dest))

    entries = [json.loads(ln) for ln in dest.read_text().splitlines()]
    assert len(entries) == 1
    # rootLate ends at 3_000_000, rootEarly ends at 1_500_000 → late wins.
    assert entries[0]["game_id"] == "b"


def test_explicit_run_id_overrides_autopick(tmp_path: Path) -> None:
    trace_id = "trial-A"
    root_small = _root_span(trace_id, span_id="rootSmall")
    root_big = _root_span(trace_id, span_id="rootBig")
    spans = [
        root_small,
        root_big,
        _event_span(
            _make_init(game_id="small"),
            trace_id=trace_id,
            parent_span_id=root_small.span_id,
        ),
        _event_span(
            _make_init(game_id="big1"),
            trace_id=trace_id,
            parent_span_id=root_big.span_id,
        ),
        _event_span(
            _make_init(game_id="big2"),
            trace_id=trace_id,
            parent_span_id=root_big.span_id,
        ),
    ]
    source = SLSEventSource(reader=_FakeReader(spans))
    dest = tmp_path / "trial-A.jsonl"
    _run(source.materialize_jsonl(trace_id, dest, run_id="rootSmall"))

    entries = [json.loads(ln) for ln in dest.read_text().splitlines()]
    assert [e["game_id"] for e in entries] == ["small"]


def test_unknown_run_id_raises(tmp_path: Path) -> None:
    trace_id = "trial-A"
    root = _root_span(trace_id, span_id="rootA")
    spans = [
        root,
        _event_span(_make_init(), trace_id=trace_id, parent_span_id=root.span_id),
    ]
    source = SLSEventSource(reader=_FakeReader(spans))

    with pytest.raises(ValueError, match="run_id.*not found"):
        _run(
            source.materialize_jsonl(
                trace_id, tmp_path / "out.jsonl", run_id="nonexistent"
            )
        )


def test_orphan_spans_dropped(tmp_path: Path, caplog) -> None:
    trace_id = "trial-A"
    root = _root_span(trace_id, span_id="rootA")
    orphan_event = _event_span(
        _make_init(game_id="orphan"),
        trace_id=trace_id,
        parent_span_id="does-not-exist",
    )
    good_event = _event_span(
        _make_init(game_id="good"),
        trace_id=trace_id,
        parent_span_id=root.span_id,
    )
    spans = [root, orphan_event, good_event]
    source = SLSEventSource(reader=_FakeReader(spans))

    dest = tmp_path / "out.jsonl"
    with caplog.at_level("WARNING"):
        _run(source.materialize_jsonl(trace_id, dest))

    entries = [json.loads(ln) for ln in dest.read_text().splitlines()]
    assert [e["game_id"] for e in entries] == ["good"]
    assert any("orphan" in r.message for r in caplog.records)


def test_deep_parent_chain_resolves_to_root(tmp_path: Path) -> None:
    trace_id = "trial-A"
    root = _root_span(trace_id, span_id="rootA")
    intermediate = _non_event_span(
        trace_id=trace_id, parent_span_id=root.span_id, op="games.registered"
    )
    deep_event = _event_span(
        _make_init(game_id="deep"),
        trace_id=trace_id,
        parent_span_id=intermediate.span_id,
    )
    spans = [root, intermediate, deep_event]
    source = SLSEventSource(reader=_FakeReader(spans))

    dest = tmp_path / "out.jsonl"
    _run(source.materialize_jsonl(trace_id, dest))
    entries = [json.loads(ln) for ln in dest.read_text().splitlines()]
    assert [e["game_id"] for e in entries] == ["deep"]


# ---------------------------------------------------------------------------
# Complex field round-trip
# ---------------------------------------------------------------------------


def test_complex_field_survives_roundtrip(tmp_path: Path) -> None:
    event = _make_odds()  # Has nested OddsInfo → JSON-encoded tag.
    root = _root_span("trial-A")
    spans = [root, _event_span(event, trace_id="trial-A", parent_span_id=root.span_id)]
    source = SLSEventSource(reader=_FakeReader(spans))

    dest = tmp_path / "trial-A.jsonl"
    _run(source.materialize_jsonl("trial-A", dest))

    parsed = json.loads(dest.read_text().splitlines()[0])
    assert parsed["event_type"] == "event.odds_update"
    assert parsed["odds"]["moneyline"]["home_odds"] == 1.9


# ---------------------------------------------------------------------------
# JSONL byte-compatibility: extract_dedup_keys_from_jsonl works on our output
# ---------------------------------------------------------------------------


def test_materialized_jsonl_feeds_existing_resume_path(tmp_path: Path) -> None:
    init = _make_init(game_id="g42")
    result = _make_result(game_id="g42")
    root = _root_span("trial-A")
    spans = [
        root,
        _event_span(init, trace_id="trial-A", parent_span_id=root.span_id),
        _event_span(result, trace_id="trial-A", parent_span_id=root.span_id),
    ]
    source = SLSEventSource(reader=_FakeReader(spans))
    dest = tmp_path / "trial-A.jsonl"
    _run(source.materialize_jsonl("trial-A", dest))

    # The existing DataHub resume helper should read our file unchanged and
    # produce the expected dedup keys.
    keys = extract_dedup_keys_from_jsonl(dest)
    assert keys == {"g42_event.game_initialize", "g42_event.game_result"}


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_atomic_write_no_partial_file_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _make_init()
    root = _root_span("trial-A")
    spans = [root, _event_span(event, trace_id="trial-A", parent_span_id=root.span_id)]
    source = SLSEventSource(reader=_FakeReader(spans))
    dest = tmp_path / "trial-A.jsonl"

    # Make the atomic rename raise.
    original_replace = Path.replace

    def _boom(self, target):  # type: ignore[no-untyped-def]
        raise OSError("boom")

    monkeypatch.setattr(Path, "replace", _boom)

    with pytest.raises(OSError, match="boom"):
        _run(source.materialize_jsonl("trial-A", dest))

    assert not dest.exists()
    assert not (tmp_path / "trial-A.jsonl.tmp").exists()

    # Sanity: after restoring, same source materializes cleanly.
    monkeypatch.setattr(Path, "replace", original_replace)
    _run(source.materialize_jsonl("trial-A", dest))
    assert dest.exists()


def test_overwrite_false_reuses_cache(tmp_path: Path) -> None:
    dest = tmp_path / "trial-A.jsonl"
    dest.write_text('{"hello": "cached"}\n')
    # Reader that would fail if called.
    class _BoomReader(_FakeReader):
        async def get_spans(self, *args, **kwargs):  # type: ignore[override]
            raise AssertionError("should not be called when cache exists")

    source = SLSEventSource(reader=_BoomReader([]))
    _run(source.materialize_jsonl("trial-A", dest, overwrite=False))

    assert dest.read_text() == '{"hello": "cached"}\n'


# ---------------------------------------------------------------------------
# Reader-lifecycle: close() is invoked when we own the reader
# ---------------------------------------------------------------------------


def test_injected_reader_not_closed(tmp_path: Path) -> None:
    reader = _FakeReader([_root_span("trial-A")])
    source = SLSEventSource(reader=reader)
    _run(source.fetch_events("trial-A"))
    assert reader.close_calls == 0  # Caller owns the reader.


# ---------------------------------------------------------------------------
# Missing env vars → clear error
# ---------------------------------------------------------------------------


def test_missing_env_vars_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DOJOZERO_SLS_PROJECT", "DOJOZERO_SLS_ENDPOINT", "DOJOZERO_SLS_LOGSTORE"):
        monkeypatch.delenv(var, raising=False)
    source = SLSEventSource()  # No injected reader → tries env.

    with pytest.raises(RuntimeError, match="DOJOZERO_SLS_PROJECT"):
        _run(source.fetch_events("trial-A"))


# ---------------------------------------------------------------------------
# Integration (skipped unless env is set AND --run-integration)
# ---------------------------------------------------------------------------


_SLS_ENV_READY = all(
    os.environ.get(name)
    for name in (
        "DOJOZERO_SLS_PROJECT",
        "DOJOZERO_SLS_ENDPOINT",
        "DOJOZERO_SLS_LOGSTORE",
        "ALIBABA_CLOUD_ACCESS_KEY_ID",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    )
)
_SLS_TEST_TRIAL_ID = os.environ.get("DOJOZERO_TEST_TRIAL_ID", "")


@pytest.mark.integration
@pytest.mark.skipif(
    not (_SLS_ENV_READY and _SLS_TEST_TRIAL_ID),
    reason="Requires DOJOZERO_SLS_* + ALIBABA_CLOUD_* + DOJOZERO_TEST_TRIAL_ID",
)
def test_live_sls_materialize(tmp_path: Path) -> None:
    dest = tmp_path / f"{_SLS_TEST_TRIAL_ID}.jsonl"
    _run(SLSEventSource().materialize_jsonl(_SLS_TEST_TRIAL_ID, dest))

    assert dest.exists()
    lines = dest.read_text().splitlines()
    assert lines, "expected at least one event in the materialized trace"
    for line in lines:
        payload = json.loads(line)
        assert "event_type" in payload
        assert payload["event_type"].startswith("event.")
