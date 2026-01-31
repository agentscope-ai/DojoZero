"""Tests for shared betting event formatters."""

from dojozero.betting._formatters import (
    format_bet_executed,
    format_bet_settled,
    format_pregame_stats,
)
from dojozero.betting._models import (
    BetExecutedPayload,
    BetOutcome,
    BetSettledPayload,
)
from dojozero.data.espn._stats_events import PreGameStatsEvent
from dojozero.data._models import (
    TeamRecentForm,
    ScheduleDensity,
    SeasonSeries,
    HomeAwaySplits,
    TeamStandings,
    TeamPlayerStats,
    TeamSeasonStats,
)


class TestFormatBetExecuted:
    def test_basic_bet_executed(self):
        payload = BetExecutedPayload(
            bet_id="bet_12345",
            event_id="game_001",
            selection="home",
            amount="100.0",
            execution_probability="0.54",
            shares="185.19",
            execution_time="2025-01-31 12:00:00+00:00",
        )
        result = format_bet_executed(payload)
        assert "[Bet Executed]" in result
        assert "bet_12345" in result
        assert "game_001" in result
        assert "home" in result
        assert "$100.0" in result
        assert "Probability: 0.54" in result
        assert "Shares: 185.19" in result
        assert "2025-01-31 12:00:00+00:00" in result

    def test_bet_executed_away_selection(self):
        payload = BetExecutedPayload(
            bet_id="bet_67890",
            event_id="game_002",
            selection="away",
            amount="250.0",
            execution_probability="0.476",
            shares="525.21",
            execution_time="2025-02-01 18:30:00+00:00",
        )
        result = format_bet_executed(payload)
        assert "away" in result
        assert "$250.0" in result
        assert "0.476" in result
        assert "525.21" in result

    def test_bet_executed_large_amount(self):
        payload = BetExecutedPayload(
            bet_id="bet_big",
            event_id="game_003",
            selection="home",
            amount="10000.0",
            execution_probability="0.667",
            shares="14992.50",
            execution_time="2025-02-01 20:00:00+00:00",
        )
        result = format_bet_executed(payload)
        assert "$10000.0" in result
        assert "14992.50" in result


class TestFormatBetSettled:
    def test_bet_settled_win(self):
        payload = BetSettledPayload(
            bet_id="bet_12345",
            event_id="game_001",
            outcome=BetOutcome.WIN,
            payout="185.0",
            winner="home",
        )
        result = format_bet_settled(payload)
        assert "[Bet Settled]" in result
        assert "bet_12345" in result
        assert "game_001" in result
        assert "WIN" in result
        assert "$185.0" in result
        assert "Winner: home" in result

    def test_bet_settled_loss(self):
        payload = BetSettledPayload(
            bet_id="bet_67890",
            event_id="game_002",
            outcome=BetOutcome.LOSS,
            payout="0.0",
            winner="away",
        )
        result = format_bet_settled(payload)
        assert "LOSS" in result
        assert "$0.0" in result
        assert "Winner: away" in result


class TestFormatPregameStats:
    def test_season_series(self):
        event = PreGameStatsEvent(
            season_series=SeasonSeries(
                home_wins=2,
                away_wins=1,
                total_games=3,
            ),
        )
        result = format_pregame_stats(event)
        assert "[Pre-Game Stats]" in result
        assert "**Season Series**" in result
        assert "2-1" in result
        assert "Home leads" in result

    def test_season_series_away_leads(self):
        event = PreGameStatsEvent(
            season_series=SeasonSeries(
                home_wins=1,
                away_wins=3,
                total_games=4,
            ),
        )
        result = format_pregame_stats(event)
        assert "Away leads" in result

    def test_season_series_tied(self):
        event = PreGameStatsEvent(
            season_series=SeasonSeries(
                home_wins=2,
                away_wins=2,
                total_games=4,
            ),
        )
        result = format_pregame_stats(event)
        assert "Tied" in result

    def test_recent_form_home(self):
        event = PreGameStatsEvent(
            home_recent_form=TeamRecentForm(
                team_name="Lakers",
                wins=8,
                losses=2,
                last_n=10,
                streak="W5",
                avg_points_scored=115.5,
                avg_points_allowed=108.2,
            ),
        )
        result = format_pregame_stats(event)
        assert "**Recent Form**" in result
        assert "Home (Lakers)" in result
        assert "8-2 L10" in result
        assert "W5" in result
        assert "115.5 PPG" in result
        assert "108.2 OPP" in result

    def test_recent_form_both_teams(self):
        event = PreGameStatsEvent(
            home_recent_form=TeamRecentForm(
                team_name="Lakers",
                wins=7,
                losses=3,
                last_n=10,
                avg_points_scored=112.0,
                avg_points_allowed=105.0,
            ),
            away_recent_form=TeamRecentForm(
                team_name="Celtics",
                wins=9,
                losses=1,
                last_n=10,
                streak="W9",
                avg_points_scored=118.5,
                avg_points_allowed=110.3,
            ),
        )
        result = format_pregame_stats(event)
        assert "Home (Lakers)" in result
        assert "Away (Celtics)" in result
        assert "7-3 L10" in result
        assert "9-1 L10" in result

    def test_schedule_stats(self):
        event = PreGameStatsEvent(
            home_schedule=ScheduleDensity(
                days_rest=2,
                is_back_to_back=False,
                games_last_7_days=3,
            ),
            away_schedule=ScheduleDensity(
                days_rest=0,
                is_back_to_back=True,
                games_last_7_days=4,
            ),
        )
        result = format_pregame_stats(event)
        assert "**Rest & Schedule**" in result
        assert "Home: 2 days rest" in result
        assert "Away: 0 days rest (B2B)" in result
        assert "3 games last 7 days" in result
        assert "4 games last 7 days" in result

    def test_team_season_stats(self):
        event = PreGameStatsEvent(
            home_team_stats=TeamSeasonStats(
                team_name="Lakers",
                stats={"avgPointsPerGame": 115.5, "avgPointsAllowed": 108.2},
                rank={"avgPointsPerGame": 3},
            ),
            away_team_stats=TeamSeasonStats(
                team_name="Celtics",
                stats={"ppg": 118.0, "oppg": 110.0},
                rank={"ppg": 1},
            ),
        )
        result = format_pregame_stats(event)
        assert "**Season Stats**" in result
        assert "Home (Lakers): 115.5 PPG (#3)" in result
        assert "Away (Celtics): 118.0 PPG (#1)" in result

    def test_home_away_splits(self):
        event = PreGameStatsEvent(
            home_splits=HomeAwaySplits(
                team_name="Lakers",
                home_record="25-5",
                away_record="15-10",
            ),
            away_splits=HomeAwaySplits(
                team_name="Celtics",
                home_record="28-2",
                away_record="20-8",
            ),
        )
        result = format_pregame_stats(event)
        assert "**Home/Away Splits**" in result
        assert "Home (Lakers): 25-5 at home, 15-10 away" in result
        assert "Away (Celtics): 28-2 at home, 20-8 away" in result

    def test_standings(self):
        event = PreGameStatsEvent(
            home_standings=TeamStandings(
                team_name="Lakers",
                conference="Western",
                conference_rank=4,
                overall_record="40-15",
                games_back=3.5,
            ),
            away_standings=TeamStandings(
                team_name="Celtics",
                conference="Eastern",
                conference_rank=1,
                overall_record="48-12",
                games_back=0,
            ),
        )
        result = format_pregame_stats(event)
        assert "**Standings**" in result
        assert "Home (Lakers): Western #4 (40-15), 3.5 GB" in result
        assert "Away (Celtics): Eastern #1 (48-12)" in result
        # No ", 0 GB" when games_back is 0
        assert ", 0 GB" not in result

    def test_key_players(self):
        event = PreGameStatsEvent(
            home_players=TeamPlayerStats(
                team_name="Lakers",
                players=[
                    {"name": "LeBron James", "ppg": 25.5},
                    {"name": "Anthony Davis", "avgPointsPerGame": 24.2},
                    {"name": "D'Angelo Russell", "ppg": 18.0},
                    {"name": "Austin Reaves", "ppg": 15.5},  # Should be truncated
                ],
            ),
            away_players=TeamPlayerStats(
                team_name="Celtics",
                players=[
                    {"name": "Jayson Tatum", "ppg": 27.8},
                    {"name": "Jaylen Brown", "ppg": 23.1},
                ],
            ),
        )
        result = format_pregame_stats(event)
        assert "**Key Players**" in result
        assert (
            "Home (Lakers): LeBron James (25.5 PPG), Anthony Davis (24.2 PPG), D'Angelo Russell (18.0 PPG)"
            in result
        )
        # Only top 3 players shown
        assert "Austin Reaves" not in result
        assert (
            "Away (Celtics): Jayson Tatum (27.8 PPG), Jaylen Brown (23.1 PPG)" in result
        )

    def test_key_players_without_ppg(self):
        event = PreGameStatsEvent(
            home_players=TeamPlayerStats(
                team_name="Lakers",
                players=[
                    {"name": "LeBron James"},
                    {"name": "Anthony Davis", "ppg": 0},
                ],
            ),
        )
        result = format_pregame_stats(event)
        # Players without ppg should still show up
        assert "LeBron James" in result
        assert "(25" not in result  # No PPG shown for LeBron

    def test_empty_pregame_stats(self):
        event = PreGameStatsEvent()
        result = format_pregame_stats(event)
        assert "[Pre-Game Stats]" in result
        # Should not crash with empty stats

    def test_comprehensive_pregame_stats(self):
        """Test with all stats populated."""
        event = PreGameStatsEvent(
            season_series=SeasonSeries(home_wins=2, away_wins=1, total_games=3),
            home_recent_form=TeamRecentForm(
                team_name="Lakers",
                wins=8,
                losses=2,
                last_n=10,
                streak="W5",
                avg_points_scored=115.5,
                avg_points_allowed=108.2,
            ),
            away_recent_form=TeamRecentForm(
                team_name="Celtics",
                wins=9,
                losses=1,
                last_n=10,
                streak="W9",
                avg_points_scored=118.5,
                avg_points_allowed=110.3,
            ),
            home_schedule=ScheduleDensity(
                days_rest=2, is_back_to_back=False, games_last_7_days=3
            ),
            away_schedule=ScheduleDensity(
                days_rest=0, is_back_to_back=True, games_last_7_days=4
            ),
            home_team_stats=TeamSeasonStats(
                team_name="Lakers",
                stats={"avgPointsPerGame": 115.5, "avgPointsAllowed": 108.2},
                rank={"avgPointsPerGame": 3},
            ),
            away_team_stats=TeamSeasonStats(
                team_name="Celtics",
                stats={"ppg": 118.0, "oppg": 110.0},
                rank={"ppg": 1},
            ),
            home_splits=HomeAwaySplits(
                team_name="Lakers", home_record="25-5", away_record="15-10"
            ),
            away_splits=HomeAwaySplits(
                team_name="Celtics", home_record="28-2", away_record="20-8"
            ),
            home_standings=TeamStandings(
                team_name="Lakers",
                conference="Western",
                conference_rank=4,
                overall_record="40-15",
                games_back=3.5,
            ),
            away_standings=TeamStandings(
                team_name="Celtics",
                conference="Eastern",
                conference_rank=1,
                overall_record="48-12",
                games_back=0,
            ),
            home_players=TeamPlayerStats(
                team_name="Lakers",
                players=[
                    {"name": "LeBron James", "ppg": 25.5},
                    {"name": "Anthony Davis", "ppg": 24.2},
                ],
            ),
            away_players=TeamPlayerStats(
                team_name="Celtics",
                players=[
                    {"name": "Jayson Tatum", "ppg": 27.8},
                ],
            ),
        )
        result = format_pregame_stats(event)
        # Verify all sections are present
        assert "**Season Series**" in result
        assert "**Recent Form**" in result
        assert "**Rest & Schedule**" in result
        assert "**Season Stats**" in result
        assert "**Home/Away Splits**" in result
        assert "**Standings**" in result
        assert "**Key Players**" in result
