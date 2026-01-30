"""Tests for NBA event formatters."""

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
)
from dojozero.data.nba._events import (
    NBAGameUpdateEvent,
    NBAPlayEvent,
    NBATeamGameStats,
)
from dojozero.data.websearch._events import (
    ExpertPredictionEvent,
    InjuryReportEvent,
    PowerRankingEvent,
)
from dojozero.nba._formatters import format_event, parse_response_content


class TestFormatInjurySummary:
    def test_with_summary_and_players(self):
        event = InjuryReportEvent(
            summary="Key injuries heading into tonight's game.",
            injured_players={
                "Lakers": ["LeBron James", "Anthony Davis"],
                "Celtics": ["Jaylen Brown"],
            },
        )
        result = format_event(event)
        assert "[Injury Report Update]" in result
        assert "Key injuries heading into tonight's game." in result
        assert "Lakers: LeBron James, Anthony Davis" in result
        assert "Celtics: Jaylen Brown" in result

    def test_summary_only(self):
        event = InjuryReportEvent(
            summary="No significant injuries reported.",
            injured_players={},
        )
        result = format_event(event)
        assert "[Injury Report Update]" in result
        assert "No significant injuries reported." in result
        assert "Injured Players" not in result

    def test_empty(self):
        event = InjuryReportEvent()
        result = format_event(event)
        assert "[Injury Report Update]" in result


class TestFormatPowerRanking:
    def test_with_rankings(self):
        event = PowerRankingEvent(
            rankings={
                "espn.com": [
                    {"rank": 1, "team": "Celtics", "record": "42-12"},
                    {"rank": 2, "team": "Thunder", "record": "40-14"},
                ],
            },
        )
        result = format_event(event)
        assert "[Power Rankings Update]" in result
        assert "Source: espn.com" in result
        assert "1. Celtics (42-12)" in result
        assert "2. Thunder (40-14)" in result

    def test_ranking_without_record(self):
        event = PowerRankingEvent(
            rankings={
                "nba.com": [{"rank": 1, "team": "Lakers"}],
            },
        )
        result = format_event(event)
        assert "1. Lakers" in result
        # No parenthetical record
        assert "()" not in result

    def test_empty_rankings(self):
        event = PowerRankingEvent(rankings={})
        result = format_event(event)
        assert "[Power Rankings Update]" in result


class TestFormatExpertPrediction:
    def test_with_predictions(self):
        event = ExpertPredictionEvent(
            predictions=[
                {
                    "source": "ESPN",
                    "expert": "Stephen A.",
                    "prediction": "Lakers win by 10",
                    "confidence": "High",
                },
            ],
        )
        result = format_event(event)
        assert "[Expert Predictions]" in result
        assert "ESPN" in result
        assert "Stephen A." in result
        assert "Lakers win by 10" in result
        assert "Confidence: High" in result

    def test_prediction_without_expert(self):
        event = ExpertPredictionEvent(
            predictions=[
                {"source": "CBS Sports", "prediction": "Celtics favored"},
            ],
        )
        result = format_event(event)
        assert "CBS Sports" in result
        assert "Celtics favored" in result

    def test_empty_predictions(self):
        event = ExpertPredictionEvent(predictions=[])
        result = format_event(event)
        assert "[Expert Predictions]" in result


class TestFormatGameInitialize:
    def test_with_team_identity(self):
        event = GameInitializeEvent(
            home_team=TeamIdentity(name="Los Angeles Lakers"),
            away_team=TeamIdentity(name="Boston Celtics"),
            game_time=datetime(2025, 1, 15, 3, 0, tzinfo=timezone.utc),
        )
        result = format_event(event)
        assert "[Game Initialized]" in result
        assert "Boston Celtics @ Los Angeles Lakers" in result
        assert "2025-01-15 03:00 UTC" in result

    def test_with_string_teams(self):
        event = GameInitializeEvent(
            home_team="Lakers",
            away_team="Celtics",
            game_time=datetime(2025, 1, 15, 3, 0, tzinfo=timezone.utc),
        )
        result = format_event(event)
        assert "Celtics @ Lakers" in result


class TestFormatGameStart:
    def test_basic(self):
        event = GameStartEvent(game_id="401610001")
        result = format_event(event)
        assert "[Game Started]" in result
        assert "401610001" in result


class TestFormatGameResult:
    def test_home_win(self):
        event = GameResultEvent(
            winner="home",
            home_score=112,
            away_score=98,
        )
        result = format_event(event)
        assert "[Game Finished]" in result
        assert "Home Team wins!" in result
        assert "112" in result
        assert "98" in result

    def test_away_win(self):
        event = GameResultEvent(winner="away", home_score=95, away_score=110)
        result = format_event(event)
        assert "Away Team wins!" in result


class TestFormatGameUpdate:
    def test_regular_quarter(self):
        event = NBAGameUpdateEvent(
            period=2,
            game_clock="5:30",
            home_team_stats=NBATeamGameStats(
                team_name="Lakers", team_tricode="LAL", score=52
            ),
            away_team_stats=NBATeamGameStats(
                team_name="Celtics", team_tricode="BOS", score=48
            ),
        )
        result = format_event(event)
        assert "[Game Update] Q2 | 5:30" in result
        assert "Lakers (LAL): 52" in result
        assert "Celtics (BOS): 48" in result

    def test_overtime(self):
        event = NBAGameUpdateEvent(
            period=5,
            game_clock="3:00",
            home_team_stats=NBATeamGameStats(
                team_name="Lakers", team_tricode="LAL", score=110
            ),
            away_team_stats=NBATeamGameStats(
                team_name="Celtics", team_tricode="BOS", score=110
            ),
        )
        result = format_event(event)
        assert "OT1" in result

    def test_no_clock(self):
        event = NBAGameUpdateEvent(
            period=1,
            game_clock="",
            home_team_stats=NBATeamGameStats(
                team_name="Lakers", team_tricode="LAL", score=0
            ),
            away_team_stats=NBATeamGameStats(
                team_name="Celtics", team_tricode="BOS", score=0
            ),
        )
        result = format_event(event)
        assert "[Game Update] Q1" in result
        # No trailing " | "
        assert "Q1 |" not in result


class TestFormatOddsUpdate:
    def test_moneyline_only(self):
        event = OddsUpdateEvent(
            odds=OddsInfo(
                moneyline=MoneylineOdds(
                    home_odds=1.50,
                    away_odds=2.80,
                    home_probability=0.667,
                    away_probability=0.357,
                ),
            ),
        )
        result = format_event(event)
        assert "[Odds Update]" in result
        assert "Home: 1.50" in result
        assert "Away: 2.80" in result
        assert "66.7% implied" in result

    def test_with_spreads(self):
        event = OddsUpdateEvent(
            odds=OddsInfo(
                spreads=[
                    SpreadOdds(
                        spread=-5.5,
                        home_odds=1.91,
                        away_odds=1.91,
                    ),
                ],
            ),
        )
        result = format_event(event)
        assert "Spread:" in result
        assert "-5.5" in result

    def test_with_totals(self):
        event = OddsUpdateEvent(
            odds=OddsInfo(
                totals=[
                    TotalOdds(
                        total=220.5,
                        over_odds=1.90,
                        under_odds=1.95,
                    ),
                ],
            ),
        )
        result = format_event(event)
        assert "Total:" in result
        assert "220.5" in result
        assert "Over: 1.90" in result
        assert "Under: 1.95" in result

    def test_full_odds(self):
        event = OddsUpdateEvent(
            odds=OddsInfo(
                moneyline=MoneylineOdds(
                    home_odds=1.50,
                    away_odds=2.80,
                    home_probability=0.667,
                    away_probability=0.357,
                ),
                spreads=[
                    SpreadOdds(spread=-6.5, home_odds=1.91, away_odds=1.91),
                ],
                totals=[
                    TotalOdds(total=215.0, over_odds=1.87, under_odds=1.98),
                ],
            ),
        )
        result = format_event(event)
        assert "Home: 1.50" in result
        assert "Spread:" in result
        assert "Total:" in result


class TestFormatPlayByPlay:
    def test_basic_play(self):
        event = NBAPlayEvent(
            period=1,
            clock="10:30",
            action_type="shot",
            player_name="LeBron James",
            team_tricode="LAL",
            description="LeBron James 3PT Jump Shot (5 PTS)",
            home_score=5,
            away_score=3,
        )
        result = format_event(event)
        assert "[Play] Q1 10:30" in result
        assert "SHOT" in result
        assert "(LAL)" in result
        assert "[LeBron James]" in result
        assert "Score: 3-5" in result

    def test_overtime_play(self):
        event = NBAPlayEvent(
            period=6,
            clock="2:00",
            action_type="foul",
            player_name="Jayson Tatum",
            team_tricode="BOS",
            description="Jayson Tatum Personal Foul",
            home_score=120,
            away_score=118,
        )
        result = format_event(event)
        assert "OT2" in result

    def test_play_without_player(self):
        event = NBAPlayEvent(
            period=2,
            clock="0:00",
            action_type="period_end",
            description="End of 2nd Quarter",
            home_score=55,
            away_score=48,
        )
        result = format_event(event)
        assert "PERIOD_END" in result
        # No empty brackets for missing player
        assert "[]" not in result


class TestFormatDefault:
    def test_unknown_event_type(self):
        """format_event falls back to JSON dump for unknown event types."""
        event = GameStartEvent(game_id="test123")
        result = format_event(event)
        assert "test123" in result


class TestFormatEventWithPrefix:
    def test_event_prefix_stripped(self):
        """event_type with 'event.' prefix is properly stripped for lookup."""
        event = GameStartEvent(game_id="401610001")
        # GameStartEvent.event_type == "event.game_start"
        # format_event strips "event." -> looks up "game_start"
        result = format_event(event)
        assert "[Game Started]" in result


class TestParseResponseContent:
    def test_none_content(self):
        text, tools = parse_response_content(None)
        assert text == ""
        assert tools is None

    def test_string_content(self):
        text, tools = parse_response_content("hello world")
        assert text == "hello world"
        assert tools is None

    def test_list_with_text(self):
        content = [
            {"type": "text", "text": "First part. "},
            {"type": "text", "text": "Second part."},
        ]
        text, tools = parse_response_content(content)
        assert text == "First part. Second part."
        assert tools is None

    def test_list_with_tool_use(self):
        content = [
            {"type": "text", "text": "I'll place a bet."},
            {"type": "tool_use", "name": "place_bet", "input": {"amount": 100}},
        ]
        text, tools = parse_response_content(content)
        assert text == "I'll place a bet."
        assert tools is not None
        assert len(tools) == 1
        assert tools[0]["name"] == "place_bet"

    def test_list_with_tool_result(self):
        content = [
            {"type": "tool_result", "tool_use_id": "abc", "content": "ok"},
        ]
        text, tools = parse_response_content(content)
        assert text == ""
        assert tools is not None
        assert len(tools) == 1

    def test_empty_list(self):
        text, tools = parse_response_content([])
        assert text == ""
        assert tools is None

    def test_list_with_non_dict_items(self):
        content = ["string_item", 42, {"type": "text", "text": "valid"}]
        text, tools = parse_response_content(content)
        assert text == "valid"
        assert tools is None
