from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dojozero.arena_server._utils import _build_upcoming_games_from_schedules


def _sched(
    game_id: str,
    sport: str,
    event_time: str,
    phase: str = "waiting",
    source_id: str = "world-cup-prediction-source",
    home: str = "Curacao",
    away: str = "Brazil",
    home_tri: str = "CUW",
    away_tri: str = "BRA",
) -> dict:
    return {
        "phase": phase,
        "sport_type": sport,
        "game_id": game_id,
        "event_time": event_time,
        "scenario_name": source_id,
        "metadata": {
            "source_id": source_id,
            "home_team": home,
            "away_team": away,
            "home_tricode": home_tri,
            "away_tricode": away_tri,
        },
    }


def test_builds_future_waiting_games_with_team_data() -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    games = _build_upcoming_games_from_schedules(
        [_sched("760473", "world_cup", future)]
    )

    assert len(games) == 1
    g = games[0]
    assert g.id == "world_cup-game-760473-upcoming"
    assert g.league == "WORLD_CUP"
    assert g.status == "upcoming"
    assert g.home_team.name == "Curacao"
    assert g.away_team.tricode == "BRA"
    assert g.home_team.logo_url.endswith("/countries/500/cuw.png")


def test_excludes_past_and_non_waiting() -> None:
    now = datetime.now(timezone.utc)
    past = (now - timedelta(hours=1)).isoformat()
    future = (now + timedelta(hours=6)).isoformat()
    schedules = [
        _sched("past", "world_cup", past),  # kickoff already passed
        _sched("done", "world_cup", future, phase="completed"),  # not waiting
        _sched("ok", "world_cup", future),
    ]
    games = _build_upcoming_games_from_schedules(schedules)
    assert [g.id for g in games] == ["world_cup-game-ok-upcoming"]


def test_dedupes_by_game_id_preferring_prediction() -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    schedules = [
        _sched("760473", "world_cup", future, source_id="world-cup-betting-source"),
        _sched("760473", "world_cup", future, source_id="world-cup-prediction-source"),
    ]
    games = _build_upcoming_games_from_schedules(schedules)
    assert len(games) == 1  # one card per match, not per trial


def test_dedupes_within_league_only() -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    schedules = [
        _sched("shared-id", "world_cup", future),
        _sched("shared-id", "nba", future),
    ]

    games = _build_upcoming_games_from_schedules(schedules)

    assert [g.id for g in games] == [
        "world_cup-game-shared-id-upcoming",
        "nba-game-shared-id-upcoming",
    ]


def test_league_filter_and_sort_order() -> None:
    now = datetime.now(timezone.utc)
    later = (now + timedelta(hours=10)).isoformat()
    sooner = (now + timedelta(hours=2)).isoformat()
    schedules = [
        _sched("wc2", "world_cup", later),
        _sched("wc1", "world_cup", sooner),
        _sched("nfl1", "nfl", sooner),
    ]
    wc = _build_upcoming_games_from_schedules(schedules, league="WORLD_CUP")
    assert [g.id for g in wc] == [
        "world_cup-game-wc1-upcoming",
        "world_cup-game-wc2-upcoming",
    ]  # league-filtered and sorted by kickoff
    nfl = _build_upcoming_games_from_schedules(schedules, league="NFL")
    assert [g.id for g in nfl] == ["nfl-game-nfl1-upcoming"]


def test_sort_uses_real_kickoff_across_timezone_offsets() -> None:
    # Two future kickoffs expressed in different offsets. The earlier *instant*
    # has the later wall-clock string (so a naive ISO-string sort would order
    # them wrong), proving the sort compares actual kickoff time.
    now = datetime.now(timezone.utc)
    tz_plus10 = timezone(timedelta(hours=10))
    earlier = (now + timedelta(hours=2)).astimezone(tz_plus10).isoformat()
    later = (now + timedelta(hours=4)).astimezone(timezone.utc).isoformat()
    assert earlier > later  # lexicographically reversed vs chronological order

    games = _build_upcoming_games_from_schedules(
        [
            _sched("earlier", "world_cup", earlier),
            _sched("later", "world_cup", later),
        ]
    )
    assert [g.id for g in games] == [
        "world_cup-game-earlier-upcoming",
        "world_cup-game-later-upcoming",
    ]


def test_contest_kind_is_set_on_upcoming_cards() -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()

    pred = _build_upcoming_games_from_schedules(
        [_sched("p1", "world_cup", future, source_id="world-cup-prediction-source")]
    )
    assert pred[0].contest_kind == "prediction"

    bet = _build_upcoming_games_from_schedules(
        [_sched("b1", "world_cup", future, source_id="world-cup-betting-source")]
    )
    assert bet[0].contest_kind == "betting"

    # Dedup prefers the prediction trial, so the surviving card is prediction.
    deduped = _build_upcoming_games_from_schedules(
        [
            _sched("d1", "world_cup", future, source_id="world-cup-betting-source"),
            _sched("d1", "world_cup", future, source_id="world-cup-prediction-source"),
        ]
    )
    assert deduped[0].contest_kind == "prediction"
