"""World Cup elimination games decided by a penalty shootout keep a tied
home_score/away_score (extra-time goals count toward the score; the shootout
itself does not), but the match still has a winner. Regression tests for
`_extract_trial_info_from_spans`/`_extract_games_from_trials` surfacing that
winner (and a "decided on penalties/extra time" note) to the games API
instead of silently dropping it -- see issue #254.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from dojozero.arena_server._utils import (
    _extract_games_from_trials,
    _extract_trial_info_from_spans,
)
from dojozero.core._tracing import SpanData
from dojozero.data._models import GameResultEvent
from dojozero.data.world_cup import WorldCupGameUpdateEvent


def _span_from_event(event: Any, *, start_time: int = 1) -> SpanData:
    """Mirror DataHub._emit_event_span()'s span serialization for a DataEvent."""
    event_dict = event.to_dict()
    tags: dict[str, Any] = {}
    for key, value in event_dict.items():
        if key in ("event_type", "timestamp"):
            continue
        if isinstance(value, (dict, list)):
            tags[f"event.{key}"] = json.dumps(value, default=str)
        else:
            tags[f"event.{key}"] = value

    return SpanData(
        trace_id="test-trial",
        span_id=f"span-{start_time}",
        operation_name=event.event_type,
        start_time=start_time,
        duration=0,
        tags=tags,
    )


def _trial_started_span(*, start_time: int = 0) -> SpanData:
    return SpanData(
        trace_id="test-trial",
        span_id="trial-started",
        operation_name="trial.started",
        start_time=start_time,
        duration=0,
        tags={
            "trial.home_tricode": "GER",
            "trial.away_tricode": "ARG",
            "trial.home_team_name": "Germany",
            "trial.away_team_name": "Argentina",
            "trial.game_date": "2026-07-01T19:00:00+00:00",
            "trial.sport_type": "world_cup",
            "trial.espn_game_id": "pk-1",
        },
    )


def _shootout_spans() -> list[SpanData]:
    """A knockout match tied 1-1 through extra time, home team wins on pens."""
    game_update = WorldCupGameUpdateEvent(
        timestamp=datetime.now(timezone.utc),
        game_id="pk-1",
        sport="world_cup",
        period=5,
        game_clock="PEN",
        home_score=1,
        away_score=1,
    )
    result = GameResultEvent(
        timestamp=datetime.now(timezone.utc),
        game_id="pk-1",
        sport="world_cup",
        winner="home",
        home_score=1,
        away_score=1,
        home_team_name="Germany",
        away_team_name="Argentina",
    )
    return [
        _trial_started_span(start_time=0),
        _span_from_event(game_update, start_time=1),
        _span_from_event(result, start_time=2),
    ]


def test_extract_trial_info_captures_shootout_winner_not_just_tied_score() -> None:
    info = _extract_trial_info_from_spans(_shootout_spans())

    assert info["phase"] == "completed"
    assert info["metadata"]["home_score"] == 1
    assert info["metadata"]["away_score"] == 1
    # The regulation/ET score is tied, but the match was NOT a draw --
    # winner must still be surfaced from the GameResultEvent.
    assert info["metadata"]["result_winner"] == "home"


class _StubTraceReader:
    def __init__(self, spans: list[SpanData]) -> None:
        self._spans = spans

    async def get_spans(
        self, trial_id: str, operation_names: list[str] | None = None
    ) -> list[SpanData]:
        return self._spans


@pytest.mark.asyncio
async def test_extract_games_from_trials_flags_penalty_shootout_result() -> None:
    spans = _shootout_spans()
    response = await _extract_games_from_trials(
        trace_reader=_StubTraceReader(spans),  # type: ignore[arg-type]
        trial_ids=["world_cup-pk-1"],
        cache=None,
    )

    assert response.live_games == []
    assert len(response.completed_games) == 1
    game = response.completed_games[0]

    # A tied 1-1 scoreline must not be reported as a draw for a knockout game.
    assert game.home_score == 1
    assert game.away_score == 1
    assert game.winning_team == "home"
    assert game.result_note == "Decided on penalties"
