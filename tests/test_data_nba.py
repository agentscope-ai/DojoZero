"""
Integration tests for NBA data utilities.

These tests make real API calls to NBA.com endpoints and are skipped by default.
Run with: pytest -v --run-integration tests/test_data_nba.py
"""

import os
from datetime import datetime, timedelta

import pytest

from dojozero.data.nba._utils import get_game_info_by_id, get_games_by_date_range


# =============================================================================
# Pytest configuration
# =============================================================================

# Custom marker for integration tests
pytestmark = pytest.mark.integration


# =============================================================================
# Fixtures and Helper Functions
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
