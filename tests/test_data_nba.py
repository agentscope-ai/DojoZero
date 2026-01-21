"""
Tests for NBA data infrastructure.

Unit tests run by default. Integration tests make real API calls and are skipped by default.
Run integration tests with: pytest -v --run-integration tests/test_data_nba.py
"""

import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from dojozero.data.nba._events import (
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    GameUpdateEvent,
    PlayByPlayEvent,
)
from dojozero.data.nba._store import NBAStore
from dojozero.data.nba._utils import get_game_info_by_id, get_games_by_date_range


# =============================================================================
# Unit Tests for NBAStore (no network required)
# =============================================================================


@pytest.fixture
def nba_store():
    """Create an NBAStore instance with mocked API."""
    mock_api = MagicMock()
    return NBAStore(store_id="test_nba_store", api=mock_api)


class TestNBAStoreParseBoxscore:
    """Tests for _parse_api_response with boxscore data."""

    def test_parse_boxscore_with_team_data(self, nba_store):
        """Test parsing boxscore with full team data."""
        boxscore_data = {
            "boxscore": {
                "gameId": "0022400123",
                "homeTeam": {
                    "teamId": 1610612747,
                    "teamName": "Lakers",
                    "teamCity": "Los Angeles",
                    "teamTricode": "LAL",
                    "statistics": {"points": 110},
                    "players": [
                        {
                            "personId": 123,
                            "name": "Player A",
                            "statistics": {"points": 25},
                        }
                    ],
                },
                "awayTeam": {
                    "teamId": 1610612744,
                    "teamName": "Warriors",
                    "teamCity": "Golden State",
                    "teamTricode": "GSW",
                    "statistics": {"points": 105},
                    "players": [
                        {
                            "personId": 456,
                            "name": "Player B",
                            "statistics": {"points": 30},
                        }
                    ],
                },
            }
        }

        # Mock get_game_info_by_id to avoid real API calls
        with patch(
            "dojozero.data.nba._utils.get_game_info_by_id",
            return_value={
                "game_time_utc": "2024-01-15T03:00:00Z",
            },
        ):
            events = nba_store._parse_api_response(boxscore_data)

        # Should emit GameUpdateEvent and GameInitializeEvent
        update_events = [e for e in events if isinstance(e, GameUpdateEvent)]
        init_events = [e for e in events if isinstance(e, GameInitializeEvent)]

        assert len(update_events) == 1
        assert len(init_events) == 1

        update = update_events[0]
        assert update.game_id == "0022400123"
        assert update.home_team["score"] == 110
        assert update.away_team["score"] == 105
        assert update.home_team["teamTricode"] == "LAL"
        assert update.away_team["teamTricode"] == "GSW"
        assert len(update.player_stats["home"]) == 1
        assert len(update.player_stats["away"]) == 1

        init = init_events[0]
        assert init.game_id == "0022400123"
        assert init.home_team == "Los Angeles Lakers"
        assert init.away_team == "Golden State Warriors"

    def test_parse_boxscore_without_team_data(self, nba_store):
        """Test parsing boxscore before game starts (no team data)."""
        boxscore_data = {
            "boxscore": {
                "gameId": "0022400123",
                "homeTeam": {},
                "awayTeam": {},
            }
        }

        # Mock get_game_info_by_id to avoid real API call
        # The function is imported inside _parse_api_response, so patch at source module
        with patch(
            "dojozero.data.nba._utils.get_game_info_by_id",
            return_value={
                "home_team": "Los Angeles Lakers",
                "away_team": "Golden State Warriors",
                "game_time_utc": "2024-01-15T03:00:00Z",
            },
        ):
            events = nba_store._parse_api_response(boxscore_data)

        # Should emit GameInitializeEvent from get_game_info_by_id fallback
        init_events = [e for e in events if isinstance(e, GameInitializeEvent)]
        assert len(init_events) == 1
        assert init_events[0].home_team == "Los Angeles Lakers"

    def test_parse_boxscore_empty_game_id(self, nba_store):
        """Test parsing boxscore with missing game ID returns empty."""
        boxscore_data = {"boxscore": {"gameId": ""}}

        events = nba_store._parse_api_response(boxscore_data)
        assert len(events) == 0

    def test_parse_boxscore_invalid_structure(self, nba_store):
        """Test parsing boxscore with invalid structure returns empty."""
        boxscore_data = {"boxscore": "invalid"}

        events = nba_store._parse_api_response(boxscore_data)
        assert len(events) == 0

    def test_game_initialize_emitted_once(self, nba_store):
        """Test that GameInitializeEvent is emitted only once per game."""
        boxscore_data = {
            "boxscore": {
                "gameId": "0022400123",
                "homeTeam": {
                    "teamId": 1,
                    "teamName": "Lakers",
                    "teamCity": "Los Angeles",
                    "teamTricode": "LAL",
                    "statistics": {"points": 50},
                    "players": [],
                },
                "awayTeam": {
                    "teamId": 2,
                    "teamName": "Warriors",
                    "teamCity": "Golden State",
                    "teamTricode": "GSW",
                    "statistics": {"points": 48},
                    "players": [],
                },
            }
        }

        # Mock get_game_info_by_id to avoid real API calls
        with patch(
            "dojozero.data.nba._utils.get_game_info_by_id",
            return_value={"game_time_utc": "2024-01-15T03:00:00Z"},
        ):
            # First call should emit GameInitializeEvent
            events1 = nba_store._parse_api_response(boxscore_data)
            init_events1 = [e for e in events1 if isinstance(e, GameInitializeEvent)]
            assert len(init_events1) == 1

            # Second call should NOT emit GameInitializeEvent
            events2 = nba_store._parse_api_response(boxscore_data)
            init_events2 = [e for e in events2 if isinstance(e, GameInitializeEvent)]
            assert len(init_events2) == 0


class TestNBAStoreParsePlayByPlay:
    """Tests for _parse_api_response with play-by-play data."""

    def test_parse_pbp_game_start_detection(self, nba_store):
        """Test that first play-by-play actions trigger GameStartEvent."""
        pbp_data = {
            "play_by_play": {
                "gameId": "0022400123",
                "actions": [
                    {
                        "actionNumber": 1,
                        "actionType": "jumpball",
                        "description": "Jump Ball",
                        "period": 1,
                        "clock": "PT12M00.00S",
                        "scoreHome": "0",
                        "scoreAway": "0",
                    }
                ],
            }
        }

        events = nba_store._parse_api_response(pbp_data)

        # Should emit GameStartEvent and PlayByPlayEvent
        start_events = [e for e in events if isinstance(e, GameStartEvent)]
        pbp_events = [e for e in events if isinstance(e, PlayByPlayEvent)]

        assert len(start_events) == 1
        assert start_events[0].event_id == "0022400123"

        assert len(pbp_events) == 1
        assert pbp_events[0].action_type == "jumpball"
        assert pbp_events[0].action_number == 1

    def test_parse_pbp_game_end_detection(self, nba_store):
        """Test that game end action triggers GameResultEvent."""
        # First, simulate game start
        nba_store._state.set_previous_status("0022400123", 2)  # In Progress
        nba_store._state.mark_pbp_available("0022400123")

        pbp_data = {
            "play_by_play": {
                "gameId": "0022400123",
                "actions": [
                    {
                        "actionNumber": 999,
                        "actionType": "game",
                        "description": "Game End",
                        "period": 4,
                        "clock": "PT00M00.00S",
                        "scoreHome": "110",
                        "scoreAway": "105",
                    }
                ],
            }
        }

        events = nba_store._parse_api_response(pbp_data)

        # Should emit GameResultEvent
        result_events = [e for e in events if isinstance(e, GameResultEvent)]
        assert len(result_events) == 1
        assert result_events[0].winner == "home"
        assert result_events[0].final_score == {"home": 110, "away": 105}

    def test_parse_pbp_away_team_wins(self, nba_store):
        """Test GameResultEvent with away team winning."""
        nba_store._state.set_previous_status("0022400123", 2)
        nba_store._state.mark_pbp_available("0022400123")

        pbp_data = {
            "play_by_play": {
                "gameId": "0022400123",
                "actions": [
                    {
                        "actionNumber": 999,
                        "actionType": "game",
                        "description": "Game End",
                        "scoreHome": "95",
                        "scoreAway": "110",
                    }
                ],
            }
        }

        events = nba_store._parse_api_response(pbp_data)
        result_events = [e for e in events if isinstance(e, GameResultEvent)]

        assert len(result_events) == 1
        assert result_events[0].winner == "away"

    def test_parse_pbp_action_deduplication(self, nba_store):
        """Test that duplicate actions are not emitted twice."""
        pbp_data = {
            "play_by_play": {
                "gameId": "0022400123",
                "actions": [
                    {
                        "actionNumber": 1,
                        "actionType": "jumpball",
                        "description": "Jump Ball",
                    },
                    {
                        "actionNumber": 2,
                        "actionType": "2pt",
                        "description": "Made Shot",
                    },
                ],
            }
        }

        # First call
        events1 = nba_store._parse_api_response(pbp_data)
        pbp_events1 = [e for e in events1 if isinstance(e, PlayByPlayEvent)]
        assert len(pbp_events1) == 2

        # Second call with same actions should return empty (deduplicated)
        events2 = nba_store._parse_api_response(pbp_data)
        pbp_events2 = [e for e in events2 if isinstance(e, PlayByPlayEvent)]
        assert len(pbp_events2) == 0

    def test_parse_pbp_incremental_actions(self, nba_store):
        """Test that only new actions are emitted on subsequent calls."""
        # First batch
        pbp_data1 = {
            "play_by_play": {
                "gameId": "0022400123",
                "actions": [
                    {
                        "actionNumber": 1,
                        "actionType": "jumpball",
                        "description": "Jump Ball",
                    },
                ],
            }
        }
        events1 = nba_store._parse_api_response(pbp_data1)
        pbp_events1 = [e for e in events1 if isinstance(e, PlayByPlayEvent)]
        assert len(pbp_events1) == 1

        # Second batch with new action
        pbp_data2 = {
            "play_by_play": {
                "gameId": "0022400123",
                "actions": [
                    {
                        "actionNumber": 1,
                        "actionType": "jumpball",
                        "description": "Jump Ball",
                    },
                    {
                        "actionNumber": 2,
                        "actionType": "2pt",
                        "description": "Made Shot",
                    },
                ],
            }
        }
        events2 = nba_store._parse_api_response(pbp_data2)
        pbp_events2 = [e for e in events2 if isinstance(e, PlayByPlayEvent)]

        # Only action 2 should be new
        assert len(pbp_events2) == 1
        assert pbp_events2[0].action_number == 2

    def test_parse_pbp_extracts_player_info(self, nba_store):
        """Test that player info is extracted from play-by-play."""
        pbp_data = {
            "play_by_play": {
                "gameId": "0022400123",
                "actions": [
                    {
                        "actionNumber": 10,
                        "actionType": "2pt",
                        "description": "LeBron James makes layup",
                        "personId": 2544,
                        "playerName": "LeBron James",
                        "teamTricode": "LAL",
                        "period": 1,
                        "clock": "PT10M30.00S",
                        "scoreHome": "2",
                        "scoreAway": "0",
                    }
                ],
            }
        }

        events = nba_store._parse_api_response(pbp_data)
        pbp_events = [e for e in events if isinstance(e, PlayByPlayEvent)]

        assert len(pbp_events) == 1
        event = pbp_events[0]
        assert event.person_id == 2544
        assert event.player_name == "LeBron James"
        assert event.team_tricode == "LAL"
        assert event.home_score == 2
        assert event.away_score == 0


class TestNBAStoreStateTransitions:
    """Tests for game state transition handling."""

    def test_game_start_not_emitted_twice(self, nba_store):
        """Test that GameStartEvent is only emitted once."""
        pbp_data = {
            "play_by_play": {
                "gameId": "0022400123",
                "actions": [{"actionNumber": 1, "actionType": "jumpball"}],
            }
        }

        # First call should emit GameStartEvent
        events1 = nba_store._parse_api_response(pbp_data)
        start_events1 = [e for e in events1 if isinstance(e, GameStartEvent)]
        assert len(start_events1) == 1

        # Second call should NOT emit GameStartEvent
        pbp_data["play_by_play"]["actions"].append(
            {"actionNumber": 2, "actionType": "2pt"}
        )
        events2 = nba_store._parse_api_response(pbp_data)
        start_events2 = [e for e in events2 if isinstance(e, GameStartEvent)]
        assert len(start_events2) == 0

    def test_game_result_not_emitted_twice(self, nba_store):
        """Test that GameResultEvent is only emitted once."""
        nba_store._state.set_previous_status("0022400123", 2)
        nba_store._state.mark_pbp_available("0022400123")

        pbp_data = {
            "play_by_play": {
                "gameId": "0022400123",
                "actions": [
                    {
                        "actionNumber": 999,
                        "actionType": "game",
                        "description": "Game End",
                        "scoreHome": "110",
                        "scoreAway": "105",
                    }
                ],
            }
        }

        # First call should emit GameResultEvent
        events1 = nba_store._parse_api_response(pbp_data)
        result_events1 = [e for e in events1 if isinstance(e, GameResultEvent)]
        assert len(result_events1) == 1

        # Second call should NOT emit GameResultEvent
        events2 = nba_store._parse_api_response(pbp_data)
        result_events2 = [e for e in events2 if isinstance(e, GameResultEvent)]
        assert len(result_events2) == 0

    def test_state_isolation_between_games(self, nba_store):
        """Test that state is tracked separately per game."""
        pbp_data_game1 = {
            "play_by_play": {
                "gameId": "0022400001",
                "actions": [{"actionNumber": 1, "actionType": "jumpball"}],
            }
        }
        pbp_data_game2 = {
            "play_by_play": {
                "gameId": "0022400002",
                "actions": [{"actionNumber": 1, "actionType": "jumpball"}],
            }
        }

        # Both games should emit GameStartEvent
        events1 = nba_store._parse_api_response(pbp_data_game1)
        events2 = nba_store._parse_api_response(pbp_data_game2)

        start_events1 = [e for e in events1 if isinstance(e, GameStartEvent)]
        start_events2 = [e for e in events2 if isinstance(e, GameStartEvent)]

        assert len(start_events1) == 1
        assert len(start_events2) == 1
        assert start_events1[0].event_id == "0022400001"
        assert start_events2[0].event_id == "0022400002"


class TestNBAStoreExtractPlayerStats:
    """Tests for _extract_player_stats_from_boxscore."""

    def test_extract_player_stats(self, nba_store):
        """Test extracting player stats from boxscore data."""
        boxscore_data = {
            "homeTeam": {
                "players": [
                    {"personId": 1, "name": "Player A"},
                    {"personId": 2, "name": "Player B"},
                ]
            },
            "awayTeam": {
                "players": [
                    {"personId": 3, "name": "Player C"},
                ]
            },
        }

        result = nba_store._extract_player_stats_from_boxscore(boxscore_data)

        assert "home" in result
        assert "away" in result
        assert len(result["home"]) == 2
        assert len(result["away"]) == 1

    def test_extract_player_stats_empty_teams(self, nba_store):
        """Test extracting player stats when teams are empty."""
        boxscore_data = {
            "homeTeam": {},
            "awayTeam": {},
        }

        result = nba_store._extract_player_stats_from_boxscore(boxscore_data)

        assert result["home"] == []
        assert result["away"] == []

    def test_extract_player_stats_invalid_players(self, nba_store):
        """Test handling invalid players data."""
        boxscore_data = {
            "homeTeam": {"players": "invalid"},
            "awayTeam": {"players": None},
        }

        result = nba_store._extract_player_stats_from_boxscore(boxscore_data)

        assert result["home"] == []
        assert result["away"] == []


# =============================================================================
# Integration Tests (require network - skipped by default)
# =============================================================================


# =============================================================================
# Integration Test Fixtures and Helper Functions
# =============================================================================


@pytest.fixture(scope="module")
def test_game_ids() -> dict[str, str]:
    """Get game IDs for testing (shared across all tests to minimize API calls)."""
    game_ids = {}

    # Get recent historical game (yesterday)
    yesterday = (datetime.now() - timedelta(days=1)).date()
    recent_games = get_games_by_date_range(yesterday, yesterday)
    if recent_games:
        game_ids["recent"] = recent_games[0]["game_id"]

    # Get older historical game (7 days ago)
    week_ago = (datetime.now() - timedelta(days=7)).date()
    old_games = get_games_by_date_range(week_ago, week_ago)
    if old_games:
        game_ids["historical"] = old_games[0]["game_id"]

    # Get very old game (30 days ago)
    month_ago = (datetime.now() - timedelta(days=30)).date()
    very_old_games = get_games_by_date_range(month_ago, month_ago)
    if very_old_games:
        game_ids["old"] = very_old_games[0]["game_id"]

    # Get future scheduled game
    start_date = (datetime.now() + timedelta(days=1)).date()
    end_date = (datetime.now() + timedelta(days=7)).date()
    future_games = get_games_by_date_range(start_date, end_date)
    for game in future_games:
        if game["game_status"] == 1:  # Scheduled
            game_ids["future"] = game["game_id"]
            break

    return game_ids


# =============================================================================
# Integration Tests for get_games_by_date_range
# =============================================================================


@pytest.mark.integration
class TestGetGamesByDateRangeIntegration:
    """Integration tests for get_games_by_date_range function."""

    def test_single_day_range(self):
        """Test fetching games for a single day."""
        yesterday = (datetime.now() - timedelta(days=1)).date()
        games = get_games_by_date_range(yesterday, yesterday)

        # Should return a list (may be empty if no games that day)
        assert isinstance(games, list)

        if games:
            # Verify structure of first game
            game = games[0]
            assert "game_id" in game
            assert "home_team" in game
            assert "away_team" in game
            assert "game_status" in game

    def test_multi_day_range(self):
        """Test fetching games across multiple days."""
        end_date = (datetime.now() - timedelta(days=1)).date()
        start_date = end_date - timedelta(days=3)

        games = get_games_by_date_range(start_date, end_date)

        assert isinstance(games, list)
        # Should have multiple games across 4 days
        assert len(games) > 0

    def test_future_date_range(self):
        """Test fetching scheduled future games."""
        start_date = (datetime.now() + timedelta(days=1)).date()
        end_date = start_date + timedelta(days=2)

        games = get_games_by_date_range(start_date, end_date)

        assert isinstance(games, list)
        # May have games scheduled

        if games:
            # Future games should have status 1 (scheduled) or 2 (in-progress)
            for game in games:
                assert game["game_status"] in [1, 2, 3]

    def test_empty_date_range(self):
        """Test date range with likely no games (offseason)."""
        # Use a date far in the future that's unlikely to have games scheduled
        start_date = (datetime.now() + timedelta(days=180)).date()
        games = get_games_by_date_range(start_date, start_date)

        # Should return empty list
        assert isinstance(games, list)
        # Likely empty during offseason
        assert len(games) >= 0


# =============================================================================
# Integration Tests for get_game_info_by_id
# =============================================================================


@pytest.mark.integration
class TestGetGameInfoByIdIntegration:
    """Integration tests for get_game_info_by_id function.

    These tests make real API calls to NBA.com and require network connectivity.
    Tests are combined to minimize API calls.
    """

    def test_historical_games_and_structure(self, test_game_ids):
        """Test fetching historical games (recent and old) and validate structure.

        This combines multiple checks in one test to minimize API calls:
        - Recent historical game (BoxScore endpoint)
        - Older historical game (BoxScore endpoint)
        - Return structure validation
        - Type checking
        """
        # Test recent historical game
        if "recent" in test_game_ids:
            recent_id = test_game_ids["recent"]
            result = get_game_info_by_id(recent_id)

            assert result is not None, f"Recent game {recent_id} should be found"
            assert result["game_id"] == recent_id
            assert result["home_team_tricode"] != ""
            assert result["away_team_tricode"] != ""
            assert result["home_team"] != ""
            assert result["away_team"] != ""

            # Validate structure
            required_keys = [
                "game_id",
                "home_team",
                "away_team",
                "home_team_tricode",
                "away_team_tricode",
                "game_date",
                "game_time_utc",
            ]
            for key in required_keys:
                assert key in result, f"Result should contain '{key}' field"

            # Check types
            assert isinstance(result["game_id"], str)
            assert isinstance(result["home_team"], str)
            assert isinstance(result["away_team"], str)
            assert isinstance(result["home_team_tricode"], str)
            assert isinstance(result["away_team_tricode"], str)
            assert isinstance(result["game_date"], str)
            assert isinstance(result["game_time_utc"], str)

        # Test older historical game
        if "historical" in test_game_ids:
            hist_id = test_game_ids["historical"]
            result = get_game_info_by_id(hist_id)

            assert result is not None, f"Historical game {hist_id} should be found"
            assert result["game_id"] == hist_id
            assert result["home_team_tricode"] != ""
            assert result["away_team_tricode"] != ""

        # Test very old game
        if "old" in test_game_ids:
            old_id = test_game_ids["old"]
            result = get_game_info_by_id(old_id)

            assert result is not None, f"Old game {old_id} should be found"
            assert result["game_id"] == old_id
            assert result["home_team_tricode"] != ""
            assert result["away_team_tricode"] != ""

        # Ensure at least one game was tested
        assert len(test_game_ids) > 0, "Should have at least one test game ID"

    def test_future_scheduled_game(self, test_game_ids):
        """Test fetching info for a future scheduled game.

        This should fall back to Scoreboard endpoint search.
        """
        if "future" not in test_game_ids:
            pytest.skip("No future scheduled game found in test data")

        future_id = test_game_ids["future"]
        result = get_game_info_by_id(future_id)

        assert result is not None, f"Game {future_id} should be found"
        assert result["game_id"] == future_id
        assert result["home_team_tricode"] != ""
        assert result["away_team_tricode"] != ""
        assert result["home_team"] != ""
        assert result["away_team"] != ""
        # Future games should have a date from Scoreboard
        assert result["game_date"] != ""

    def test_invalid_and_malformed_game_ids(self):
        """Test that invalid and malformed game IDs return None.

        Combines multiple invalid ID checks to minimize redundant tests.
        """
        invalid_ids = [
            "0029999999",  # Invalid: season 9999, game 9999
            "invalid",  # Malformed: not numeric
            "12345",  # Malformed: too short
            "00225001234567890",  # Malformed: too long
            "",  # Malformed: empty string
        ]

        for game_id in invalid_ids:
            result = get_game_info_by_id(game_id)
            assert result is None, (
                f"Invalid/malformed game ID '{game_id}' should return None"
            )

    def test_proxy_support(self, test_game_ids):
        """Test that proxy parameter works (if DOJOZERO_PROXY_URL env var is set)."""
        proxy = os.getenv("DOJOZERO_PROXY_URL")
        if not proxy:
            pytest.skip("DOJOZERO_PROXY_URL environment variable not set")

        if "recent" not in test_game_ids:
            pytest.skip("No recent game ID available for proxy test")

        game_id = test_game_ids["recent"]
        result = get_game_info_by_id(game_id, proxy=proxy)

        assert result is not None, f"Game {game_id} should be found with proxy"
        assert result["game_id"] == game_id

    def test_consistency_across_calls(self, test_game_ids):
        """Test that multiple calls return consistent data."""
        if "recent" not in test_game_ids:
            pytest.skip("No recent game ID available for consistency test")

        game_id = test_game_ids["recent"]

        # Call the function twice
        result1 = get_game_info_by_id(game_id)
        result2 = get_game_info_by_id(game_id)

        assert result1 is not None
        assert result2 is not None

        # Results should be identical
        assert result1["game_id"] == result2["game_id"]
        assert result1["home_team"] == result2["home_team"]
        assert result1["away_team"] == result2["away_team"]
        assert result1["home_team_tricode"] == result2["home_team_tricode"]
        assert result1["away_team_tricode"] == result2["away_team_tricode"]
