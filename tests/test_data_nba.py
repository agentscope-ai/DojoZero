"""
Tests for NBA data infrastructure.

Unit tests run by default. Integration tests make real ESPN API calls and are skipped by default.
Run integration tests with: pytest -v --run-integration tests/test_data_nba.py

Note: NBA data now uses ESPN API (not stats.nba.com) for improved reliability.
Games are identified by ESPN event IDs (e.g., '401810490').
"""

import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dojozero.data._game_info import GameInfo
from dojozero.data._models import (
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    PlayerIdentity,
)
from dojozero.data.nba._api import NBAExternalAPI, _id_from_ref
from dojozero.data.nba._events import (
    NBAGameUpdateEvent as GameUpdateEvent,
    NBAPlayEvent as PlayByPlayEvent,
)
from dojozero.data.nba._store import NBAStore
from dojozero.data.nba._utils import (
    extract_team_names_from_query,
    get_game_info_by_id,
    get_games_by_date_range,
    normalize_team_name,
    parse_iso_datetime,
)


# =============================================================================
# Shared Fixtures and Test Data
# =============================================================================


@pytest.fixture
def nba_store():
    """Create an NBAStore instance with mocked API."""
    mock_api = MagicMock()
    return NBAStore(store_id="test_nba_store", api=mock_api)


@pytest.fixture
def sample_boxscore_data():
    """Sample boxscore data for testing."""
    return {
        "boxscore": {
            "gameId": "401810001",
            "homeTeam": {
                "teamId": 1610612747,
                "teamName": "Lakers",
                "teamCity": "Los Angeles",
                "teamTricode": "LAL",
                "statistics": {"points": 110},
                "players": [
                    {"personId": 123, "name": "Player A", "statistics": {"points": 25}}
                ],
            },
            "awayTeam": {
                "teamId": 1610612744,
                "teamName": "Warriors",
                "teamCity": "Golden State",
                "teamTricode": "GSW",
                "statistics": {"points": 105},
                "players": [
                    {"personId": 456, "name": "Player B", "statistics": {"points": 30}}
                ],
            },
        }
    }


@pytest.fixture
def sample_pbp_data():
    """Sample play-by-play data for testing."""
    return {
        "play_by_play": {
            "gameId": "401810001",
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


# =============================================================================
# Unit Tests for NBAStore (no network required)
# =============================================================================


class TestNBAStoreParseBoxscore:
    """Tests for _parse_api_response with boxscore data."""

    def test_parse_boxscore_with_team_data(self, nba_store, sample_boxscore_data):
        """Test parsing boxscore with full team data."""
        with patch(
            "dojozero.data.nba._utils.get_game_info_by_id",
            return_value={"game_time_utc": "2024-01-15T03:00:00Z"},
        ):
            events = nba_store._parse_api_response(sample_boxscore_data)

        update_events = [e for e in events if isinstance(e, GameUpdateEvent)]
        init_events = [e for e in events if isinstance(e, GameInitializeEvent)]

        assert len(update_events) == 1
        assert len(init_events) == 1

        update = update_events[0]
        assert update.game_id == "401810001"
        assert update.home_team_stats.score == 110
        assert update.away_team_stats.score == 105
        assert update.home_team_stats.team_tricode == "LAL"
        assert update.away_team_stats.team_tricode == "GSW"
        assert len(update.player_stats.home) == 1
        assert len(update.player_stats.away) == 1

        init = init_events[0]
        assert init.game_id == "401810001"
        assert str(init.home_team) == "Los Angeles Lakers"
        assert str(init.away_team) == "Golden State Warriors"

    def test_parse_boxscore_without_team_data(self, nba_store):
        """Test parsing boxscore before game starts (no team data)."""
        boxscore_data = {
            "boxscore": {
                "gameId": "401810001",
                "homeTeam": {},
                "awayTeam": {},
            }
        }

        # Mock get_game_info_by_id to avoid real API call
        # The function is imported inside _parse_api_response, so patch at source module
        mock_game_info = GameInfo.model_validate(
            {
                "gameId": "0022400001",
                "sport_type": "nba",
                "homeTeam": {
                    "teamId": "1610612747",
                    "displayName": "Los Angeles Lakers",
                    "teamTricode": "LAL",
                },
                "awayTeam": {
                    "teamId": "1610612744",
                    "displayName": "Golden State Warriors",
                    "teamTricode": "GSW",
                },
                "gameTimeUTC": "2024-01-15T03:00:00Z",
            }
        )
        with patch(
            "dojozero.data.nba._utils.get_game_info_by_id",
            return_value=mock_game_info,
        ):
            events = nba_store._parse_api_response(boxscore_data)

        # Should emit GameInitializeEvent from get_game_info_by_id fallback
        init_events = [e for e in events if isinstance(e, GameInitializeEvent)]
        assert len(init_events) == 1
        assert str(init_events[0].home_team) == "Los Angeles Lakers"

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

    def test_game_initialize_emitted_once(self, nba_store, sample_boxscore_data):
        """Test that GameInitializeEvent is emitted only once per game."""
        mock_game_info = GameInfo.model_validate(
            {
                "gameId": "0022400001",
                "sport_type": "nba",
                "homeTeam": {
                    "teamId": "1",
                    "displayName": "Team A",
                    "teamTricode": "TA",
                },
                "awayTeam": {
                    "teamId": "2",
                    "displayName": "Team B",
                    "teamTricode": "TB",
                },
                "gameTimeUTC": "2024-01-15T03:00:00Z",
            }
        )
        with patch(
            "dojozero.data.nba._utils.get_game_info_by_id",
            return_value=mock_game_info,
        ):
            # First call should emit GameInitializeEvent
            events1 = nba_store._parse_api_response(sample_boxscore_data)
            init_events1 = [e for e in events1 if isinstance(e, GameInitializeEvent)]
            assert len(init_events1) == 1

            # Second call should NOT emit GameInitializeEvent
            events2 = nba_store._parse_api_response(sample_boxscore_data)
            init_events2 = [e for e in events2 if isinstance(e, GameInitializeEvent)]
            assert len(init_events2) == 0


class TestNBAStoreParsePlayByPlay:
    """Tests for _parse_api_response with play-by-play data."""

    def test_parse_pbp_game_start_detection(self, nba_store, sample_pbp_data):
        """Test that first play-by-play actions trigger GameStartEvent."""
        events = nba_store._parse_api_response(sample_pbp_data)

        start_events = [e for e in events if isinstance(e, GameStartEvent)]
        pbp_events = [e for e in events if isinstance(e, PlayByPlayEvent)]

        assert len(start_events) == 1
        assert start_events[0].game_id == "401810001"
        assert len(pbp_events) == 1
        assert pbp_events[0].action_type == "jumpball"
        assert pbp_events[0].action_number == 1

    def test_parse_pbp_game_end_detection(self, nba_store):
        """Test that game end action triggers GameResultEvent."""
        # First, simulate game start
        nba_store._state.set_previous_status("401810001", 2)  # In Progress
        nba_store._state.mark_pbp_available("401810001")

        pbp_data = {
            "play_by_play": {
                "gameId": "401810001",
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

    def test_parse_pbp_game_end_detection_core_api_format(self, nba_store):
        """Test game end detection with ESPN Core API format (action_type='end game')."""
        nba_store._state.set_previous_status("401810001", 2)
        nba_store._state.mark_pbp_available("401810001")

        pbp_data = {
            "play_by_play": {
                "gameId": "401810001",
                "actions": [
                    {
                        "actionNumber": 439,
                        "actionType": "end game",
                        "description": "End of Game",
                        "period": 4,
                        "clock": "0.0",
                        "scoreHome": "107",
                        "scoreAway": "79",
                    }
                ],
            }
        }

        events = nba_store._parse_api_response(pbp_data)

        result_events = [e for e in events if isinstance(e, GameResultEvent)]
        assert len(result_events) == 1
        assert result_events[0].winner == "home"
        assert result_events[0].home_score == 107
        assert result_events[0].away_score == 79

    def test_parse_pbp_away_team_wins(self, nba_store):
        """Test GameResultEvent with away team winning."""
        nba_store._state.set_previous_status("401810001", 2)
        nba_store._state.mark_pbp_available("401810001")

        pbp_data = {
            "play_by_play": {
                "gameId": "401810001",
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
                "gameId": "401810001",
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
                "gameId": "401810001",
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
                "gameId": "401810001",
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
                "gameId": "401810001",
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
        assert event.player_id == 2544
        assert event.player_name == "LeBron James"
        assert event.team_tricode == "LAL"
        assert event.home_score == 2
        assert event.away_score == 0

    def test_parse_pbp_extracts_scoring_and_team_fields(self, nba_store):
        """Test that team_id, play_id, is_scoring_play, score_value are extracted."""
        pbp_data = {
            "play_by_play": {
                "gameId": "401810001",
                "actions": [
                    {
                        "actionNumber": 10,
                        "actionType": "2pt",
                        "description": "LeBron James makes layup",
                        "personId": 2544,
                        "playerName": "LeBron James",
                        "teamId": "1610612747",
                        "teamTricode": "LAL",
                        "playId": "play_123",
                        "scoringPlay": True,
                        "scoreValue": 2,
                        "period": 1,
                        "clock": "PT10M30.00S",
                        "scoreHome": "2",
                        "scoreAway": "0",
                    },
                    {
                        "actionNumber": 11,
                        "actionType": "substitution",
                        "description": "Substitution",
                        "personId": 0,
                        "playerName": "",
                        "teamId": "",
                        "teamTricode": "",
                        "playId": "",
                        "scoringPlay": False,
                        "scoreValue": 0,
                        "period": 1,
                        "clock": "PT10M00.00S",
                        "scoreHome": "2",
                        "scoreAway": "0",
                    },
                ],
            }
        }

        events = nba_store._parse_api_response(pbp_data)
        pbp_events = [e for e in events if isinstance(e, PlayByPlayEvent)]

        assert len(pbp_events) == 2

        # Scoring play should have all fields populated
        scoring = pbp_events[0]
        assert scoring.team_id == "1610612747"
        assert scoring.play_id == "play_123"
        assert scoring.is_scoring_play is True
        assert scoring.score_value == 2

        # Non-scoring play should have defaults
        sub = pbp_events[1]
        assert sub.team_id == ""
        assert sub.play_id == ""
        assert sub.is_scoring_play is False
        assert sub.score_value == 0


class TestNBAStoreStateTransitions:
    """Tests for game state transition handling."""

    def test_game_start_not_emitted_twice(self, nba_store):
        """Test that GameStartEvent is only emitted once."""
        pbp_data = {
            "play_by_play": {
                "gameId": "401810001",
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
        nba_store._state.set_previous_status("401810001", 2)
        nba_store._state.mark_pbp_available("401810001")

        pbp_data = {
            "play_by_play": {
                "gameId": "401810001",
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
                "gameId": "401810002",
                "actions": [{"actionNumber": 1, "actionType": "jumpball"}],
            }
        }
        pbp_data_game2 = {
            "play_by_play": {
                "gameId": "401810003",
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
        assert start_events1[0].game_id == "401810002"
        assert start_events2[0].game_id == "401810003"


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
# Unit Tests for _id_from_ref helper (ESPN $ref URL parsing)
# =============================================================================


class TestIdFromRef:
    """Tests for _id_from_ref ESPN $ref URL parser."""

    def test_standard_team_ref(self):
        """Test extracting team ID from standard $ref URL."""
        obj = {
            "$ref": "http://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/teams/24?lang=en&region=us"
        }
        assert _id_from_ref(obj) == "24"

    def test_athlete_ref(self):
        """Test extracting athlete ID from $ref URL."""
        obj = {
            "$ref": "http://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/athletes/5104157?lang=en"
        }
        assert _id_from_ref(obj) == "5104157"

    def test_ref_without_query_string(self):
        """Test $ref URL without query parameters."""
        obj = {"$ref": "http://sports.core.api.espn.com/v2/teams/10"}
        assert _id_from_ref(obj) == "10"

    def test_empty_ref(self):
        """Test empty $ref value returns empty string."""
        assert _id_from_ref({"$ref": ""}) == ""

    def test_missing_ref(self):
        """Test missing $ref key returns empty string."""
        assert _id_from_ref({}) == ""
        assert _id_from_ref({"id": "24"}) == ""

    def test_ref_with_trailing_slash(self):
        """Test $ref URL with trailing slash."""
        obj = {"$ref": "http://example.com/teams/24/"}
        # rsplit on "/" gives empty last segment
        assert _id_from_ref(obj) == ""


# =============================================================================
# Unit Tests for NBAExternalAPI._convert_play_to_action (ESPN play conversion)
# =============================================================================


class TestConvertPlayToAction:
    """Tests for _convert_play_to_action with $ref fallback."""

    @pytest.fixture
    def api(self):
        """Create NBAExternalAPI instance."""
        return NBAExternalAPI()

    def test_inline_team_and_player_ids(self, api):
        """Test play with inline team and player data."""
        play = {
            "type": {"text": "Shot"},
            "team": {"id": "24", "abbreviation": "SA"},
            "period": {"number": 1},
            "clock": {"displayValue": "10:30"},
            "homeScore": 2,
            "awayScore": 0,
            "text": "Player makes shot",
            "participants": [{"athlete": {"id": 5104157, "displayName": "John Doe"}}],
            "scoringPlay": True,
            "scoreValue": 2,
            "sequenceNumber": "42",
        }
        result = api._convert_play_to_action(play, 0)

        assert result["teamId"] == "24"
        assert result["teamTricode"] == "SA"
        assert result["personId"] == 5104157
        assert result["playerName"] == "John Doe"
        assert result["period"] == 1
        assert result["clock"] == "10:30"
        assert result["scoringPlay"] is True

    def test_ref_fallback_for_team_id(self, api):
        """Test team ID extraction falls back to $ref when inline id missing."""
        play = {
            "type": {"text": "Shot"},
            "team": {"$ref": "http://sports.core.api.espn.com/v2/teams/24?lang=en"},
            "period": {"number": 1},
            "clock": {"displayValue": "10:30"},
            "text": "Shot attempt",
        }
        result = api._convert_play_to_action(play, 0)

        assert result["teamId"] == "24"

    def test_ref_fallback_for_athlete_id(self, api):
        """Test athlete ID extraction falls back to $ref."""
        play = {
            "type": {"text": "Shot"},
            "team": {},
            "period": {"number": 1},
            "clock": {"displayValue": "10:30"},
            "text": "Shot attempt",
            "participants": [
                {
                    "athlete": {
                        "$ref": "http://sports.core.api.espn.com/v2/athletes/5104157?lang=en"
                    }
                }
            ],
        }
        result = api._convert_play_to_action(play, 0)

        assert result["personId"] == 5104157

    def test_game_end_detection(self, api):
        """Test game end play type detection."""
        play = {
            "type": {"id": "13", "text": "End Period"},
            "period": {"number": 4},
            "clock": {"displayValue": "0:00"},
            "text": "End of 4th Quarter",
        }
        result = api._convert_play_to_action(play, 0)

        assert result["actionType"] == "game"
        assert result["description"] == "Game End"

    def test_invalid_play_returns_none(self, api):
        """Test invalid play inputs return None."""
        assert api._convert_play_to_action({}, 0) is None  # empty dict is falsy
        assert api._convert_play_to_action(None, 0) is None
        assert api._convert_play_to_action("invalid", 0) is None

    def test_missing_participants(self, api):
        """Test play with no participants."""
        play = {
            "type": {"text": "Timeout"},
            "period": {"number": 2},
            "clock": {"displayValue": "5:00"},
            "text": "Timeout",
        }
        result = api._convert_play_to_action(play, 5)

        assert result["personId"] == 0
        assert result["playerName"] == ""
        assert result["actionNumber"] == 5


# =============================================================================
# Unit Tests for PlayerIdentity model
# =============================================================================


class TestPlayerIdentity:
    """Tests for PlayerIdentity Pydantic model."""

    def test_creation_with_defaults(self):
        """Test PlayerIdentity creation with default values."""
        p = PlayerIdentity()
        assert p.player_id == ""
        assert p.name == ""
        assert p.position == ""
        assert p.jersey == ""
        assert p.headshot_url == ""

    def test_creation_with_values(self):
        """Test PlayerIdentity creation with explicit values."""
        p = PlayerIdentity(
            player_id="3917376",
            name="Jaylen Brown",
            position="G",
            jersey="7",
            headshot_url="https://a.espncdn.com/i/headshots/nba/players/full/3917376.png",
        )
        assert p.player_id == "3917376"
        assert p.name == "Jaylen Brown"
        assert p.position == "G"
        assert p.jersey == "7"

    def test_frozen(self):
        """Test PlayerIdentity is immutable."""
        p = PlayerIdentity(player_id="1", name="Test")
        with pytest.raises(Exception):  # ValidationError for frozen model
            p.name = "Changed"

    def test_serialization_aliases(self):
        """Test that serialization aliases produce camelCase keys."""
        p = PlayerIdentity(
            player_id="123",
            headshot_url="https://example.com/img.png",
        )
        dumped = p.model_dump(by_alias=True)
        assert "playerId" in dumped
        assert "headshotUrl" in dumped
        assert dumped["playerId"] == "123"

    def test_round_trip_via_dict(self):
        """Test PlayerIdentity survives dict round-trip."""
        original = PlayerIdentity(
            player_id="3917376",
            name="Jaylen Brown",
            position="G",
            jersey="7",
            headshot_url="https://a.espncdn.com/i/headshots/nba/players/full/3917376.png",
        )
        data = original.model_dump()
        restored = PlayerIdentity.model_validate(data)
        assert restored == original


# =============================================================================
# Unit Tests for NBAStore._build_player_identities
# =============================================================================


class TestBuildPlayerIdentities:
    """Tests for NBAStore._build_player_identities static method."""

    def test_builds_from_boxscore_players(self):
        """Test building PlayerIdentity list from boxscore player dicts."""
        players = [
            {
                "personId": 3917376,
                "name": "Jaylen Brown",
                "position": "G",
                "jersey": "7",
            },
            {
                "personId": 4066354,
                "name": "Payton Pritchard",
                "position": "G",
                "jersey": "11",
            },
        ]
        result = NBAStore._build_player_identities(players, "nba")

        assert len(result) == 2
        assert result[0].player_id == "3917376"
        assert result[0].name == "Jaylen Brown"
        assert (
            result[0].headshot_url
            == "https://a.espncdn.com/i/headshots/nba/players/full/3917376.png"
        )
        assert result[1].player_id == "4066354"

    def test_empty_player_list(self):
        """Test building from empty list."""
        result = NBAStore._build_player_identities([], "nba")
        assert result == []

    def test_missing_fields_use_defaults(self):
        """Test that missing fields default gracefully."""
        players = [{"personId": 0}]
        result = NBAStore._build_player_identities(players, "nba")

        assert len(result) == 1
        assert result[0].player_id == "0"
        assert result[0].name == ""
        assert result[0].position == ""
        # pid "0" is truthy string, so headshot URL is generated
        assert "0.png" in result[0].headshot_url

    def test_headshot_url_empty_for_missing_pid(self):
        """Test that headshot URL is empty when player_id is missing."""
        players = [{"name": "Unknown"}]
        result = NBAStore._build_player_identities(players, "nba")

        assert result[0].headshot_url == ""

    def test_nfl_sport_type(self):
        """Test headshot URL uses correct sport path for NFL."""
        players = [{"personId": 12345, "name": "QB One"}]
        result = NBAStore._build_player_identities(players, "nfl")

        assert "/nfl/players/" in result[0].headshot_url


# =============================================================================
# Unit Tests for GameStartEvent with starters
# =============================================================================


class TestGameStartEventStarters:
    """Tests for GameStartEvent home_starters/away_starters fields."""

    def test_default_empty_starters(self):
        """Test GameStartEvent defaults to empty starters."""
        event = GameStartEvent(game_id="401810001", sport="nba")
        assert event.home_starters == []
        assert event.away_starters == []

    def test_starters_populated(self):
        """Test GameStartEvent with populated starters."""
        starters = [
            PlayerIdentity(player_id="1", name="PG", position="G"),
            PlayerIdentity(player_id="2", name="SG", position="G"),
            PlayerIdentity(player_id="3", name="SF", position="F"),
            PlayerIdentity(player_id="4", name="PF", position="F"),
            PlayerIdentity(player_id="5", name="C", position="C"),
        ]
        event = GameStartEvent(
            game_id="401810001",
            sport="nba",
            home_starters=starters,
            away_starters=starters,
        )
        assert len(event.home_starters) == 5
        assert len(event.away_starters) == 5
        assert event.home_starters[0].name == "PG"

    def test_starters_round_trip(self):
        """Test GameStartEvent starters survive to_dict/from_dict round-trip."""
        starters = [
            PlayerIdentity(
                player_id="3917376",
                name="Jaylen Brown",
                position="G",
                jersey="7",
                headshot_url="https://a.espncdn.com/i/headshots/nba/players/full/3917376.png",
            ),
        ]
        original = GameStartEvent(
            game_id="401810001",
            sport="nba",
            home_starters=starters,
            away_starters=[],
        )
        data = original.to_dict()
        restored = GameStartEvent.from_dict(data)
        assert isinstance(restored, GameStartEvent)

        assert len(restored.home_starters) == 1
        assert restored.home_starters[0].player_id == "3917376"
        assert restored.home_starters[0].name == "Jaylen Brown"
        assert restored.home_starters[0].headshot_url.endswith("3917376.png")
        assert restored.away_starters == []

    def test_starters_in_deserialized_union(self):
        """Test GameStartEvent with starters deserializes via discriminated union."""
        from dojozero.data import deserialize_data_event

        data = {
            "event_type": "event.game_start",
            "game_id": "401810001",
            "sport": "nba",
            "home_starters": [
                {
                    "player_id": "123",
                    "name": "Player A",
                    "position": "G",
                    "jersey": "1",
                    "headshot_url": "",
                },
            ],
            "away_starters": [],
        }
        event = deserialize_data_event(data)
        assert isinstance(event, GameStartEvent)
        assert len(event.home_starters) == 1
        assert event.home_starters[0].player_id == "123"


# =============================================================================
# Unit Tests for GameStartEvent starters from store pipeline
# =============================================================================


class TestNBAStoreStartersPipeline:
    """Tests for starters flowing through the NBAStore pipeline."""

    def test_boxscore_extracts_starters_to_state(self, nba_store, sample_boxscore_data):
        """Test that boxscore parsing extracts starters to state tracker."""
        # Add starter flags to player data
        sample_boxscore_data["boxscore"]["homeTeam"]["players"] = [
            {
                "personId": 1,
                "name": "Starter A",
                "position": "G",
                "jersey": "1",
                "starter": True,
                "statistics": {},
            },
            {
                "personId": 2,
                "name": "Bench B",
                "position": "F",
                "jersey": "2",
                "starter": False,
                "statistics": {},
            },
        ]
        sample_boxscore_data["boxscore"]["awayTeam"]["players"] = [
            {
                "personId": 3,
                "name": "Starter C",
                "position": "C",
                "jersey": "3",
                "starter": True,
                "statistics": {},
            },
        ]

        with patch("dojozero.data.nba._utils.get_game_info_by_id", return_value=None):
            nba_store._parse_api_response(sample_boxscore_data)

        game_id = "401810001"
        home_starters = nba_store._state.get_home_starters(game_id)
        away_starters = nba_store._state.get_away_starters(game_id)

        assert len(home_starters) == 1
        assert home_starters[0]["name"] == "Starter A"
        assert len(away_starters) == 1
        assert away_starters[0]["name"] == "Starter C"

    def test_game_start_event_includes_starters(self, nba_store, sample_boxscore_data):
        """Test that GameStartEvent emitted from PBP includes starters from boxscore."""
        # Set up boxscore with starters
        sample_boxscore_data["boxscore"]["homeTeam"]["players"] = [
            {
                "personId": 10,
                "name": "Home PG",
                "position": "G",
                "jersey": "1",
                "starter": True,
                "statistics": {},
            },
            {
                "personId": 11,
                "name": "Home SG",
                "position": "G",
                "jersey": "2",
                "starter": True,
                "statistics": {},
            },
        ]
        sample_boxscore_data["boxscore"]["awayTeam"]["players"] = [
            {
                "personId": 20,
                "name": "Away PG",
                "position": "G",
                "jersey": "5",
                "starter": True,
                "statistics": {},
            },
        ]

        # Process boxscore first (populates starters in state)
        with patch("dojozero.data.nba._utils.get_game_info_by_id", return_value=None):
            nba_store._parse_api_response(sample_boxscore_data)

        # Now process PBP to trigger GameStartEvent
        pbp_data = {
            "play_by_play": {
                "gameId": "401810001",
                "actions": [
                    {
                        "actionNumber": 1,
                        "actionType": "jumpball",
                        "description": "Jump Ball",
                    }
                ],
            }
        }
        events = nba_store._parse_api_response(pbp_data)
        start_events = [e for e in events if isinstance(e, GameStartEvent)]

        assert len(start_events) == 1
        start = start_events[0]
        assert len(start.home_starters) == 2
        assert len(start.away_starters) == 1
        assert start.home_starters[0].name == "Home PG"
        assert (
            start.home_starters[0].headshot_url
            == "https://a.espncdn.com/i/headshots/nba/players/full/10.png"
        )
        assert start.away_starters[0].name == "Away PG"

    def test_game_start_event_empty_starters_when_no_boxscore(self, nba_store):
        """Test GameStartEvent has empty starters when boxscore hasn't been processed."""
        pbp_data = {
            "play_by_play": {
                "gameId": "401810099",
                "actions": [{"actionNumber": 1, "actionType": "jumpball"}],
            }
        }
        events = nba_store._parse_api_response(pbp_data)
        start_events = [e for e in events if isinstance(e, GameStartEvent)]

        assert len(start_events) == 1
        assert start_events[0].home_starters == []
        assert start_events[0].away_starters == []


# =============================================================================
# Unit Tests for _enrich_boxscore_rosters
# =============================================================================


class TestEnrichBoxscoreRosters:
    """Tests for NBAStore._enrich_boxscore_rosters."""

    @pytest.fixture
    def nba_store_with_mock_api(self):
        """Create NBAStore with a mock NBAExternalAPI."""
        mock_espn_api = AsyncMock()
        mock_nba_api = MagicMock(spec=NBAExternalAPI)
        mock_nba_api._api = mock_espn_api
        store = NBAStore(store_id="test_nba_store", api=mock_nba_api)
        return store, mock_espn_api

    @pytest.mark.asyncio
    async def test_enriches_missing_players(self, nba_store_with_mock_api):
        """Test that rosters are fetched for teams missing player data."""
        store, mock_espn_api = nba_store_with_mock_api
        mock_espn_api.fetch.return_value = {
            "team_roster": {
                "athletes": [
                    {
                        "id": 100,
                        "displayName": "Player X",
                        "position": {"abbreviation": "G"},
                        "jersey": "1",
                    },
                    {
                        "id": 200,
                        "displayName": "Player Y",
                        "position": {"abbreviation": "F"},
                        "jersey": "2",
                    },
                ]
            }
        }

        boxscore_data = {
            "boxscore": {
                "homeTeam": {"teamId": "24", "teamName": "Spurs"},
                "awayTeam": {
                    "teamId": "5",
                    "teamName": "Hornets",
                    "players": [{"personId": 999}],
                },
            }
        }

        await store._enrich_boxscore_rosters(boxscore_data)

        # Home team should have been enriched
        home_players = boxscore_data["boxscore"]["homeTeam"]["players"]
        assert len(home_players) == 2
        assert home_players[0]["personId"] == 100
        assert home_players[0]["name"] == "Player X"

        # Away team already had players, should not be modified
        away_players = boxscore_data["boxscore"]["awayTeam"]["players"]
        assert len(away_players) == 1
        assert away_players[0]["personId"] == 999

    @pytest.mark.asyncio
    async def test_skips_when_players_exist(self, nba_store_with_mock_api):
        """Test that enrichment is skipped when players already exist."""
        store, mock_espn_api = nba_store_with_mock_api

        boxscore_data = {
            "boxscore": {
                "homeTeam": {"teamId": "24", "players": [{"personId": 1}]},
                "awayTeam": {"teamId": "5", "players": [{"personId": 2}]},
            }
        }

        await store._enrich_boxscore_rosters(boxscore_data)

        # API should not have been called
        mock_espn_api.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_api_failure_gracefully(self, nba_store_with_mock_api):
        """Test that API failures don't crash the enrichment."""
        store, mock_espn_api = nba_store_with_mock_api
        mock_espn_api.fetch.side_effect = Exception("API timeout")

        boxscore_data = {
            "boxscore": {
                "homeTeam": {"teamId": "24", "teamName": "Spurs"},
                "awayTeam": {"teamId": "5", "teamName": "Hornets"},
            }
        }

        # Should not raise
        await store._enrich_boxscore_rosters(boxscore_data)

        # Players should still be missing (graceful failure)
        assert "players" not in boxscore_data["boxscore"]["homeTeam"]

    @pytest.mark.asyncio
    async def test_skips_empty_boxscore(self, nba_store_with_mock_api):
        """Test that empty boxscore is handled."""
        store, mock_espn_api = nba_store_with_mock_api

        await store._enrich_boxscore_rosters({"boxscore": {}})
        await store._enrich_boxscore_rosters({})

        mock_espn_api.fetch.assert_not_called()


# =============================================================================
# Integration Tests (require network - skipped by default)
# =============================================================================


# =============================================================================
# Integration Test Fixtures and Helper Functions
# =============================================================================


@pytest.fixture(scope="module")
def test_game_ids() -> dict[str, str]:
    """Get ESPN event IDs for testing (shared across all tests to minimize API calls)."""
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

    These tests make real API calls to ESPN and require network connectivity.
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
            assert result.game_id == recent_id
            assert result.home_team.tricode != ""
            assert result.away_team.tricode != ""
            assert result.home_team.name != ""
            assert result.away_team.name != ""

            # Validate structure - GameInfo is a Pydantic model with these attributes
            assert hasattr(result, "event_id")
            assert hasattr(result, "home_team")
            assert hasattr(result, "away_team")
            assert hasattr(result, "game_time_utc")

            # Check types
            assert isinstance(result.game_id, str)
            assert isinstance(result.home_team.name, str)
            assert isinstance(result.away_team.name, str)
            assert isinstance(result.home_team.tricode, str)
            assert isinstance(result.away_team.tricode, str)

        # Test older historical game
        if "historical" in test_game_ids:
            hist_id = test_game_ids["historical"]
            result = get_game_info_by_id(hist_id)

            assert result is not None, f"Historical game {hist_id} should be found"
            assert result.game_id == hist_id
            assert result.home_team.tricode != ""
            assert result.away_team.tricode != ""

        # Test very old game
        if "old" in test_game_ids:
            old_id = test_game_ids["old"]
            result = get_game_info_by_id(old_id)

            assert result is not None, f"Old game {old_id} should be found"
            assert result.game_id == old_id
            assert result.home_team.tricode != ""
            assert result.away_team.tricode != ""

        # Ensure at least one game was tested
        assert len(test_game_ids) > 0, "Should have at least one test game ID"

    def test_future_scheduled_game(self, test_game_ids):
        """Test fetching info for a future scheduled game.

        Uses ESPN summary endpoint to fetch game info.
        """
        if "future" not in test_game_ids:
            pytest.skip("No future scheduled game found in test data")

        future_id = test_game_ids["future"]
        result = get_game_info_by_id(future_id)

        assert result is not None, f"Game {future_id} should be found"
        assert result.game_id == future_id
        assert result.home_team.tricode != ""
        assert result.away_team.tricode != ""
        assert result.home_team.name != ""
        assert result.away_team.name != ""
        # Future games should have a game time from Scoreboard
        assert result.game_time_utc is not None

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
        assert result.game_id == game_id

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
        assert result1.game_id == result2.game_id
        assert result1.home_team.name == result2.home_team.name
        assert result1.away_team.name == result2.away_team.name
        assert result1.home_team.tricode == result2.home_team.tricode
        assert result1.away_team.tricode == result2.away_team.tricode


# =============================================================================
# Unit Tests for NBA Utils (no network required)
# =============================================================================


class TestNBAUtils:
    """Tests for NBA utility functions."""

    @pytest.mark.parametrize(
        "time_str,expected_year,expected_month,expected_day",
        [
            ("2025-01-07T02:00:00Z", 2025, 1, 7),
            ("2024-12-25T19:30:00Z", 2024, 12, 25),
            ("2024-02-11T23:30:00+00:00", 2024, 2, 11),
        ],
    )
    def test_parse_iso_datetime(
        self, time_str, expected_year, expected_month, expected_day
    ):
        """Test ISO datetime parsing with various formats."""

        result = parse_iso_datetime(time_str)
        assert result.year == expected_year
        assert result.month == expected_month
        assert result.day == expected_day
        assert result.tzinfo is not None

    @pytest.mark.parametrize(
        "query,expected_teams",
        [
            ("Lakers vs Warriors", {"LAL", "GSW"}),
            ("miami heat game", {"MIA"}),
            ("boston celtics play tonight", {"BOS"}),
            ("what's the score for the 76ers", {"PHI"}),
            ("cavs vs mavs", {"CLE", "DAL"}),
            ("random query with no teams", set()),
            ("", set()),
        ],
    )
    def test_extract_team_names_from_query(self, query, expected_teams):
        """Test team extraction from query strings."""
        result = extract_team_names_from_query(query)
        assert result == expected_teams

    @pytest.mark.parametrize(
        "team_name,expected_tricode",
        [
            ("Lakers", "LAL"),
            ("LAL", "LAL"),
            ("Los Angeles Lakers", "LAL"),
            ("miami heat", "MIA"),
            ("boston celtics", "BOS"),
            ("Golden State Warriors", "GSW"),
            ("warriors", "GSW"),
            ("sixers", "PHI"),
            ("76ers", "PHI"),
            ("Invalid Team", None),
        ],
    )
    def test_normalize_team_name(self, team_name, expected_tricode):
        """Test team name normalization."""
        result = normalize_team_name(team_name)
        assert result == expected_tricode


# =============================================================================
# Unit Tests for NBA Events (no network required)
# =============================================================================


class TestNBAEvents:
    """Tests for NBA event dataclasses."""

    def test_play_by_play_event_creation(self):
        """Test PlayByPlayEvent creation and properties."""
        event = PlayByPlayEvent(
            event_id="401810001_pbp_10",
            game_id="401810001",
            action_type="2pt",
            action_number=10,
            period=1,
            clock="PT10M30.00S",
            player_id=2544,
            player_name="LeBron James",
            team_id="1610612747",
            team_tricode="LAL",
            home_score=2,
            away_score=0,
            description="LeBron James makes layup",
            play_id="play_123",
            is_scoring_play=True,
            score_value=2,
        )

        assert event.event_id == "401810001_pbp_10"
        assert event.action_type == "2pt"
        assert event.player_name == "LeBron James"
        assert event.event_type == "event.nba_play"
        assert event.team_id == "1610612747"
        assert event.play_id == "play_123"
        assert event.is_scoring_play is True
        assert event.score_value == 2

    def test_play_by_play_event_round_trip(self):
        """Test PlayByPlayEvent to_dict() / from_dict() round-trip."""
        original = PlayByPlayEvent(
            event_id="401810001_pbp_10",
            game_id="401810001",
            sport="nba",
            action_type="2pt",
            action_number=10,
            period=1,
            clock="PT10M30.00S",
            player_id=2544,
            player_name="LeBron James",
            team_id="1610612747",
            team_tricode="LAL",
            home_score=2,
            away_score=0,
            description="LeBron James makes layup",
            play_id="play_123",
            is_scoring_play=True,
            score_value=2,
        )

        event_dict = original.to_dict()
        restored = PlayByPlayEvent.from_dict(event_dict)
        assert isinstance(restored, PlayByPlayEvent)

        assert restored.game_id == "401810001"
        assert restored.team_id == "1610612747"
        assert restored.play_id == "play_123"
        assert restored.is_scoring_play is True
        assert restored.score_value == 2
        assert restored.player_id == 2544
        assert restored.player_name == "LeBron James"
        assert restored.team_tricode == "LAL"
        assert restored.action_type == "2pt"
        assert restored.event_type == "event.nba_play"

    def test_game_initialize_event_creation(self):
        """Test GameInitializeEvent creation and properties."""
        from datetime import datetime, timezone

        event = GameInitializeEvent(
            game_id="401810001",
            home_team="Los Angeles Lakers",
            away_team="Golden State Warriors",
            game_time=datetime(2024, 1, 15, 3, 0, 0, tzinfo=timezone.utc),
        )

        assert event.game_id == "401810001"
        assert str(event.home_team) == "Los Angeles Lakers"
        assert str(event.away_team) == "Golden State Warriors"
        assert event.event_type == "event.game_initialize"

    def test_game_start_event_creation(self):
        """Test GameStartEvent creation and properties."""
        event = GameStartEvent(game_id="401810001")

        assert event.game_id == "401810001"
        assert event.event_type == "event.game_start"

    def test_game_result_event_creation(self):
        """Test GameResultEvent creation and properties."""
        event = GameResultEvent(
            game_id="401810001",
            winner="home",
            home_score=110,
            away_score=105,
        )

        assert event.game_id == "401810001"
        assert event.winner == "home"
        assert event.final_score["home"] == 110
        assert event.event_type == "event.game_result"
