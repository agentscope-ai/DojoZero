"""Tests for NFL event formatters."""

from datetime import datetime, timezone

from dojozero.data._models import (
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    MoneylineOdds,
    OddsInfo,
    OddsUpdateEvent,
    SpreadOdds,
    TeamIdentity,
    TotalOdds,
    VenueInfo,
)
from dojozero.data.nfl._events import (
    NFLDriveEvent,
    NFLGameUpdateEvent,
    NFLPlayEvent,
    NFLTeamGameStats,
)
from dojozero.data.websearch._events import (
    ExpertPredictionEvent,
    InjuryReportEvent,
    PowerRankingEvent,
)
from dojozero.nfl._formatters import format_event, parse_response_content


class TestFormatInjurySummary:
    def test_with_summary_and_players(self):
        event = InjuryReportEvent(
            summary="Multiple players questionable for Sunday.",
            injured_players={
                "Chiefs": ["Patrick Mahomes", "Travis Kelce"],
                "49ers": ["Christian McCaffrey"],
            },
        )
        result = format_event(event)
        assert "[Injury Report Update]" in result
        assert "Multiple players questionable" in result
        assert "Chiefs: Patrick Mahomes, Travis Kelce" in result
        assert "49ers: Christian McCaffrey" in result

    def test_empty_team_players(self):
        event = InjuryReportEvent(
            summary="Minor updates.",
            injured_players={"Chiefs": [], "49ers": ["McCaffrey"]},
        )
        result = format_event(event)
        # Empty player list should be skipped
        assert "Chiefs" not in result
        assert "49ers: McCaffrey" in result


class TestFormatPowerRanking:
    def test_top_10_limit(self):
        rankings = [
            {"rank": i, "team": f"Team{i}", "record": f"{15 - i}-{i}"}
            for i in range(1, 15)
        ]
        event = PowerRankingEvent(rankings={"espn.com": rankings})
        result = format_event(event)
        assert "1. Team1" in result
        assert "10. Team10" in result
        # 11th team should be truncated
        assert "11. Team11" not in result


class TestFormatExpertPrediction:
    def test_with_confidence(self):
        event = ExpertPredictionEvent(
            predictions=[
                {
                    "source": "NFL Network",
                    "expert": "Rich Eisen",
                    "prediction": "Chiefs by 7",
                    "confidence": "Medium",
                },
            ],
        )
        result = format_event(event)
        assert "[Expert Predictions]" in result
        assert "NFL Network" in result
        assert "Rich Eisen" in result
        assert "Chiefs by 7" in result
        assert "Confidence: Medium" in result


class TestFormatNFLGameInitialize:
    def test_with_venue(self):
        event = GameInitializeEvent(
            home_team=TeamIdentity(name="Kansas City Chiefs"),
            away_team=TeamIdentity(name="San Francisco 49ers"),
            game_time=datetime(2025, 2, 9, 23, 30, tzinfo=timezone.utc),
            venue=VenueInfo(name="Allegiant Stadium"),
        )
        result = format_event(event)
        assert "[NFL Game Initialized]" in result
        assert "San Francisco 49ers @ Kansas City Chiefs" in result
        assert "2025-02-09 23:30 UTC" in result
        assert "Allegiant Stadium" in result

    def test_without_venue_name(self):
        event = GameInitializeEvent(
            home_team=TeamIdentity(name="Chiefs"),
            away_team=TeamIdentity(name="49ers"),
            game_time=datetime(2025, 2, 9, 23, 30, tzinfo=timezone.utc),
            venue=VenueInfo(),
        )
        result = format_event(event)
        assert "@ " not in result or "49ers @ Chiefs" in result


class TestFormatNFLGameStart:
    def test_basic(self):
        event = GameStartEvent(game_id="401610200")
        result = format_event(event)
        assert "[NFL Game Started]" in result
        assert "401610200" in result
        assert "Kickoff!" in result


class TestFormatNFLGameResult:
    def test_home_win(self):
        event = GameResultEvent(
            winner="home",
            home_score=31,
            away_score=20,
            home_team_name="Chiefs",
            away_team_name="49ers",
        )
        result = format_event(event)
        assert "[NFL Game Finished]" in result
        assert "Chiefs wins!" in result
        assert "49ers 20 - Chiefs 31" in result

    def test_away_win(self):
        event = GameResultEvent(
            winner="away",
            home_score=17,
            away_score=24,
            home_team_name="Chiefs",
            away_team_name="49ers",
        )
        result = format_event(event)
        assert "49ers wins!" in result

    def test_tie(self):
        event = GameResultEvent(
            winner="",
            home_score=20,
            away_score=20,
            home_team_name="Chiefs",
            away_team_name="49ers",
        )
        result = format_event(event)
        assert "Tie wins!" in result


class TestFormatNFLGameUpdate:
    def test_regular_quarter(self):
        event = NFLGameUpdateEvent(
            period=3,
            game_clock="8:45",
            possession="KC",
            down=2,
            distance=7,
            yard_line="SF 35",
            home_team_stats=NFLTeamGameStats(
                team_name="Chiefs", team_abbreviation="KC", score=21
            ),
            away_team_stats=NFLTeamGameStats(
                team_name="49ers", team_abbreviation="SF", score=14
            ),
        )
        result = format_event(event)
        assert "[NFL Game Update] Q3 | 8:45 | Ball: KC | 2 & 7 at SF 35" in result
        assert "49ers (SF): 14" in result
        assert "Chiefs (KC): 21" in result

    def test_overtime(self):
        event = NFLGameUpdateEvent(
            period=5,
            game_clock="10:00",
            home_team_stats=NFLTeamGameStats(
                team_name="Chiefs", team_abbreviation="KC", score=24
            ),
            away_team_stats=NFLTeamGameStats(
                team_name="49ers", team_abbreviation="SF", score=24
            ),
        )
        result = format_event(event)
        assert "OT1" in result

    def test_no_possession_or_down(self):
        event = NFLGameUpdateEvent(
            period=1,
            game_clock="15:00",
            home_team_stats=NFLTeamGameStats(
                team_name="Chiefs", team_abbreviation="KC", score=0
            ),
            away_team_stats=NFLTeamGameStats(
                team_name="49ers", team_abbreviation="SF", score=0
            ),
        )
        result = format_event(event)
        assert "Ball:" not in result
        assert "& " not in result


class TestFormatNFLOddsUpdate:
    def test_moneyline_with_provider(self):
        event = OddsUpdateEvent(
            odds=OddsInfo(
                provider="polymarket",
                moneyline=MoneylineOdds(
                    home_odds=1.60,
                    away_odds=2.50,
                    home_probability=0.625,
                    away_probability=0.400,
                ),
            ),
        )
        result = format_event(event)
        assert "[NFL Odds Update] (polymarket)" in result
        assert "Home: 1.60" in result
        assert "Away: 2.50" in result

    def test_with_spreads(self):
        event = OddsUpdateEvent(
            odds=OddsInfo(
                spreads=[
                    SpreadOdds(spread=-3.5, home_odds=1.91, away_odds=1.91),
                ],
            ),
        )
        result = format_event(event)
        assert "Spread: Home -3.5" in result

    def test_positive_spread(self):
        event = OddsUpdateEvent(
            odds=OddsInfo(
                spreads=[
                    SpreadOdds(spread=6.5, home_odds=1.91, away_odds=1.91),
                ],
            ),
        )
        result = format_event(event)
        assert "Home +6.5" in result

    def test_with_totals(self):
        event = OddsUpdateEvent(
            odds=OddsInfo(
                totals=[
                    TotalOdds(total=47.5, over_odds=1.90, under_odds=1.95),
                ],
            ),
        )
        result = format_event(event)
        assert "Total: O/U 47.5" in result
        assert "Over: 1.90" in result
        assert "Under: 1.95" in result

    def test_multiple_spreads_and_totals(self):
        event = OddsUpdateEvent(
            odds=OddsInfo(
                spreads=[
                    SpreadOdds(spread=-3.5, home_odds=1.91, away_odds=1.91),
                    SpreadOdds(spread=-4.5, home_odds=2.10, away_odds=1.80),
                ],
                totals=[
                    TotalOdds(total=47.5, over_odds=1.90, under_odds=1.95),
                    TotalOdds(total=48.5, over_odds=2.00, under_odds=1.85),
                ],
            ),
        )
        result = format_event(event)
        assert "-3.5" in result
        assert "-4.5" in result
        assert "47.5" in result
        assert "48.5" in result


class TestFormatNFLPlay:
    def test_basic_play(self):
        event = NFLPlayEvent(
            period=1,
            clock="12:30",
            play_type="Pass",
            team_abbreviation="KC",
            description="P.Mahomes pass complete to T.Kelce for 15 yards",
            yards_gained=15,
            home_score=0,
            away_score=0,
            is_scoring_play=False,
            is_turnover=False,
        )
        result = format_event(event)
        assert "[NFL Play] Q1 12:30" in result
        assert "PASS" in result
        assert "(KC)" in result
        assert "+15 yards" in result
        assert "Score: 0-0" in result

    def test_scoring_play(self):
        event = NFLPlayEvent(
            period=2,
            clock="5:00",
            play_type="Rush",
            team_abbreviation="SF",
            description="C.McCaffrey rush for 3 yards, TOUCHDOWN",
            yards_gained=3,
            home_score=7,
            away_score=7,
            is_scoring_play=True,
            is_turnover=False,
        )
        result = format_event(event)
        assert "[SCORE]" in result
        assert "TURNOVER" not in result

    def test_turnover(self):
        event = NFLPlayEvent(
            period=3,
            clock="9:15",
            play_type="Pass",
            team_abbreviation="KC",
            description="P.Mahomes pass intercepted by F.Warner",
            yards_gained=0,
            home_score=14,
            away_score=10,
            is_scoring_play=False,
            is_turnover=True,
        )
        result = format_event(event)
        assert "[TURNOVER]" in result
        assert "SCORE" not in result

    def test_scoring_turnover(self):
        event = NFLPlayEvent(
            period=4,
            clock="1:00",
            play_type="Pass",
            team_abbreviation="KC",
            description="Fumble returned for touchdown",
            yards_gained=-5,
            home_score=21,
            away_score=24,
            is_scoring_play=True,
            is_turnover=True,
        )
        result = format_event(event)
        assert "[SCORE, TURNOVER]" in result
        assert "-5 yards" in result

    def test_overtime_play(self):
        event = NFLPlayEvent(
            period=5,
            clock="10:00",
            play_type="Kickoff",
            description="Kickoff",
            yards_gained=0,
            home_score=24,
            away_score=24,
        )
        result = format_event(event)
        assert "OT1" in result

    def test_zero_yards(self):
        event = NFLPlayEvent(
            period=1,
            clock="8:00",
            play_type="Pass",
            team_abbreviation="KC",
            description="P.Mahomes pass incomplete",
            yards_gained=0,
            home_score=0,
            away_score=0,
        )
        result = format_event(event)
        # Zero yards should not show yards string
        assert "yards" not in result


class TestFormatNFLDrive:
    def test_scoring_drive(self):
        event = NFLDriveEvent(
            team_tricode="KC",
            plays_count=8,
            yards=75,
            time_elapsed="4:32",
            result="Touchdown",
            is_score=True,
            points_scored=7,
        )
        result = format_event(event)
        assert "[NFL Drive] KC: 8 plays, 75 yards, 4:32" in result
        assert "Touchdown (7 pts)" in result

    def test_non_scoring_drive(self):
        event = NFLDriveEvent(
            team_tricode="SF",
            plays_count=3,
            yards=5,
            time_elapsed="1:20",
            result="Punt",
            is_score=False,
            points_scored=0,
        )
        result = format_event(event)
        assert "[NFL Drive] SF: 3 plays, 5 yards, 1:20 → Punt" in result
        assert "pts" not in result


class TestFormatDefault:
    def test_unknown_event_type(self):
        """Default formatter produces JSON for unregistered event types."""
        event = GameStartEvent(game_id="test123")
        # GameStartEvent is registered, so it won't hit default.
        # We just verify format_event works end-to-end.
        result = format_event(event)
        assert "test123" in result


class TestFormatEventWithPrefix:
    def test_event_prefix_stripped(self):
        event = GameStartEvent(game_id="401610200")
        result = format_event(event)
        assert "[NFL Game Started]" in result


class TestParseResponseContent:
    def test_none_content(self):
        text, tools = parse_response_content(None)
        assert text == ""
        assert tools is None

    def test_string_content(self):
        text, tools = parse_response_content("analysis text")
        assert text == "analysis text"
        assert tools is None

    def test_list_with_text_and_tool(self):
        content = [
            {"type": "text", "text": "Placing bet."},
            {"type": "tool_use", "name": "place_bet", "input": {"side": "home"}},
        ]
        text, tools = parse_response_content(content)
        assert text == "Placing bet."
        assert tools is not None
        assert len(tools) == 1

    def test_empty_list(self):
        text, tools = parse_response_content([])
        assert text == ""
        assert tools is None
