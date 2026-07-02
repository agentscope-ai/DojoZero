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


def _e2e_summary(
    status: str, hs: str, aws: str, hw: bool | None = None, aw: bool | None = None
) -> dict[str, Any]:
    def comp(side: str, score: str, winner: bool | None) -> dict[str, Any]:
        c: dict[str, Any] = {
            "homeAway": side,
            "score": score,
            "team": {
                "id": "449" if side == "home" else "2869",
                "displayName": "Netherlands" if side == "home" else "Morocco",
                "abbreviation": "NED" if side == "home" else "MAR",
            },
        }
        if winner is not None:
            c["winner"] = winner
        return c

    return {
        "header": {
            "id": "760488",
            "season": {"year": 2026, "type": 3},
            "competitions": [
                {
                    "id": "760488",
                    "date": "2026-06-30T19:00Z",
                    "status": {"type": {"name": status}},
                    "competitors": [comp("home", hs, hw), comp("away", aws, aw)],
                }
            ],
        }
    }


def _e2e_play(slug: str, period: int, clock: str, text: str) -> dict[str, Any]:
    return {
        "eventId": "760488",
        "items": [
            {
                "id": f"{slug}-{period}",
                "type": {"id": "999", "text": text, "type": slug},
                "period": {"number": period},
                "clock": {"displayValue": clock},
                "homeScore": 1,
                "awayScore": 1,
                "text": text,
            }
        ],
    }


@pytest.mark.asyncio
async def test_end_to_end_store_to_arena_penalty_shootout() -> None:
    """Drive a real ESPN-shaped shootout sequence (cf. event 760488, Netherlands
    1-1 Morocco, Morocco win on pens) through WorldCupStore, then the arena
    extraction -- proving the whole issue #254 fix: the store emits the true
    winner (not "even") and the arena surfaces it with the penalties note.
    """
    from dojozero.data.world_cup import WorldCupStore

    store = WorldCupStore(store_id="e2e_pens", league="fifa.world")

    store_events: list[Any] = []
    # Regulation ends level while ESPN is still in-progress -> deferred.
    store_events += store._parse_api_response(
        {"summary": _e2e_summary("STATUS_SECOND_HALF", "1", "1")}
    )
    store_events += store._parse_api_response(
        {"plays": _e2e_play("end-regular-time", 2, "90'+7'", "Level at 1-1.")}
    )
    assert not any(isinstance(e, GameResultEvent) for e in store_events)

    # Shootout final: ESPN marks FINAL_PEN and flags Morocco (away) as winner.
    store_events += store._parse_api_response(
        {"summary": _e2e_summary("STATUS_FINAL_PEN", "1", "1", hw=False, aw=True)}
    )
    store_events += store._parse_api_response(
        {"plays": _e2e_play("end-shootout", 5, "PEN", "Penalty Shootout ends, 2-3.")}
    )
    # A trailing final summary now that PBP has reached period 5, so the games
    # API sees a period-5 update (the source of the "penalties" note).
    store_events += store._parse_api_response(
        {"summary": _e2e_summary("STATUS_FINAL_PEN", "1", "1", hw=False, aw=True)}
    )

    # The store emits exactly one result, and it is the true winner -- not even.
    results = [e for e in store_events if isinstance(e, GameResultEvent)]
    assert len(results) == 1
    assert results[0].winner == "away"

    # Serialize the store's own events to spans and run the arena extraction.
    spans: list[SpanData] = [
        SpanData(
            trace_id="e2e",
            span_id="trial-started",
            operation_name="trial.started",
            start_time=0,
            duration=0,
            tags={
                "trial.home_tricode": "NED",
                "trial.away_tricode": "MAR",
                "trial.home_team_name": "Netherlands",
                "trial.away_team_name": "Morocco",
                "trial.game_date": "2026-06-30T19:00:00+00:00",
                "trial.sport_type": "world_cup",
                "trial.espn_game_id": "760488",
            },
        )
    ]
    for idx, ev in enumerate(store_events, start=1):
        spans.append(_span_from_event(ev, start_time=idx))

    response = await _extract_games_from_trials(
        trace_reader=_StubTraceReader(spans),  # type: ignore[arg-type]
        trial_ids=["world_cup-760488"],
        cache=None,
    )

    assert len(response.completed_games) == 1
    game = response.completed_games[0]
    assert game.home_score == 1
    assert game.away_score == 1
    assert game.winning_team == "away"
    assert game.result_note == "Decided on penalties"
