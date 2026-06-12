"""Tests for World Cup event formatters."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dojozero.data._models import (
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    MoneylineOdds,
    OddsInfo,
    OddsUpdateEvent,
    TeamIdentity,
)
from dojozero.data.world_cup._events import (
    SoccerGamePlayerStats,
    SoccerTeamMatchStats,
    WorldCupGameUpdateEvent,
    WorldCupPlayEvent,
)
from dojozero.world_cup._formatters import (
    _period_label,
    format_event,
    parse_response_content,
)


class TestPeriodLabel:
    @pytest.mark.parametrize(
        "period,expected",
        [
            (0, "?"),
            (1, "1H"),
            (2, "2H"),
            (3, "ET1"),
            (4, "ET2"),
            (5, "PEN"),
            (9, "P9"),
        ],
    )
    def test_period_label(self, period: int, expected: str) -> None:
        assert _period_label(period) == expected


class TestFormatLifecycleEvents:
    def test_game_initialize_uses_team_names(self) -> None:
        ev = GameInitializeEvent(
            game_id="g1",
            sport="world_cup",
            home_team=TeamIdentity(name="Ecuador"),
            away_team=TeamIdentity(name="Argentina"),
            game_time=datetime(2025, 9, 9, 23, 0, tzinfo=timezone.utc),
        )
        out = format_event(ev)
        assert "Match Initialized" in out
        assert "Ecuador" in out
        assert "Argentina" in out

    def test_game_start_includes_match_id(self) -> None:
        ev = GameStartEvent(game_id="684665", sport="world_cup")
        assert "Kickoff" in format_event(ev)
        assert "684665" in format_event(ev)

    def test_game_result_home_winner(self) -> None:
        ev = GameResultEvent(
            game_id="g1",
            sport="world_cup",
            winner="home",
            home_score=1,
            away_score=0,
            home_team_name="Ecuador",
            away_team_name="Argentina",
        )
        out = format_event(ev)
        assert "Full Time" in out
        assert "Ecuador" in out
        # 1 - 0 (final score formatting)
        assert "1" in out and "0" in out

    def test_game_result_draw(self) -> None:
        ev = GameResultEvent(
            game_id="g1",
            sport="world_cup",
            winner="even",
            home_score=1,
            away_score=1,
            home_team_name="Ecuador",
            away_team_name="Argentina",
        )
        assert "Draw" in format_event(ev)


class TestFormatGameUpdate:
    def test_includes_period_label_and_curated_stats(self) -> None:
        ev = WorldCupGameUpdateEvent(
            game_id="g1",
            sport="world_cup",
            period=2,
            game_clock="90'+4'",
            home_score=1,
            away_score=0,
            home_team_stats=SoccerTeamMatchStats(
                team_name="Ecuador",
                team_tricode="ECU",
                score=1,
                possession_pct=42.6,
                total_shots=11,
                shots_on_target=4,
            ),
            away_team_stats=SoccerTeamMatchStats(
                team_name="Argentina",
                team_tricode="ARG",
                score=0,
                possession_pct=57.4,
                total_shots=8,
                shots_on_target=2,
            ),
            player_stats=SoccerGamePlayerStats(),
        )
        out = format_event(ev)
        assert "2H" in out
        assert "90'+4'" in out
        assert "Ecuador (ECU): 1" in out
        assert "Argentina (ARG): 0" in out
        assert "42.6" in out  # possession pct rendered


class TestFormatPlayByPlay:
    def test_basic_play(self) -> None:
        ev = WorldCupPlayEvent(
            game_id="g1",
            play_id="p1",
            sport="world_cup",
            period=1,
            clock="45'+13'",
            action_type="Penalty - Scored",
            player_name="Enner Valencia",
            team_tricode="ECU",
            home_score=1,
            away_score=0,
            is_scoring_play=True,
            description="Goal! Ecuador 1, Argentina 0.",
        )
        out = format_event(ev)
        assert "1H" in out
        assert "PENALTY - SCORED" in out
        assert "Enner Valencia" in out
        assert "ECU" in out
        assert "0-1" in out  # away-home

    def test_extra_time_period(self) -> None:
        ev = WorldCupPlayEvent(
            game_id="g1",
            play_id="p1",
            sport="world_cup",
            period=3,
            clock="105'",
            action_type="Shot On Target",
        )
        assert "ET1 105'" in format_event(ev)

    def test_play_without_player_renders(self) -> None:
        ev = WorldCupPlayEvent(
            game_id="g1",
            play_id="p1",
            sport="world_cup",
            period=1,
            action_type="Throw In",
        )
        out = format_event(ev)
        assert "THROW IN" in out


class TestFormatOddsUpdate:
    def test_moneyline_only(self) -> None:
        ev = OddsUpdateEvent(
            game_id="g1",
            sport="world_cup",
            odds=OddsInfo(
                provider="polymarket",
                moneyline=MoneylineOdds(
                    home_probability=0.6,
                    away_probability=0.4,
                    home_odds=1.67,
                    away_odds=2.50,
                ),
            ),
        )
        out = format_event(ev)
        assert "Odds Update" in out
        assert "Home" in out
        assert "Away" in out
        assert "60.0%" in out


class TestFormatDefault:
    def test_unknown_event_falls_through(self) -> None:
        ev = WorldCupPlayEvent(
            game_id="g1",
            play_id="p1",
            sport="world_cup",
            action_type="Free Kick",
            period=1,
        )
        # Force an unrecognized event_type to trigger the default branch.
        object.__setattr__(ev, "event_type", "event.unknown_world_cup_event")
        out = format_event(ev)
        assert "event.unknown_world_cup_event" in out


class TestParseResponseContent:
    def test_none_content(self) -> None:
        assert parse_response_content(None) == ("", None)

    def test_string_content(self) -> None:
        assert parse_response_content("hi") == ("hi", None)

    def test_list_with_text_and_tool_use(self) -> None:
        content = [
            {"type": "text", "text": "Hello"},
            {"type": "tool_use", "name": "place_bet"},
            {"type": "text", "text": " world"},
        ]
        text, calls = parse_response_content(content)
        assert text == "Hello world"
        assert calls is not None
        assert len(calls) == 1

    def test_list_with_no_recognized_items(self) -> None:
        content = [{"type": "image"}, "not a dict"]
        text, calls = parse_response_content(content)
        assert text == ""
        assert calls is None
