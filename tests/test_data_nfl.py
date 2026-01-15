"""Tests for NFL data infrastructure."""

from unittest.mock import MagicMock

import pytest

from dojozero.data.nfl import (
    NFLDriveEvent,
    NFLExternalAPI,
    NFLGameInitializeEvent,
    NFLGameResultEvent,
    NFLGameStartEvent,
    NFLGameStateTracker,
    NFLOddsUpdateEvent,
    NFLPlayEvent,
    NFLStore,
    american_odds_to_probability,
    format_game_clock,
    get_team_abbreviation,
    get_team_division,
    get_team_name,
    parse_iso_datetime,
    probability_to_american_odds,
    spread_to_favorite,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def nfl_store():
    """Create an NFLStore instance with mocked API."""
    mock_api = MagicMock()
    return NFLStore(store_id="test_nfl_store", api=mock_api)


@pytest.fixture
def state_tracker():
    """Create a fresh NFLGameStateTracker instance."""
    return NFLGameStateTracker()


# =============================================================================
# State Tracker Tests
# =============================================================================


class TestNFLGameStateTracker:
    """Tests for NFLGameStateTracker."""

    def test_get_previous_status_returns_none_for_unseen_game(self, state_tracker):
        """Test that unseen games return None for status."""
        assert state_tracker.get_previous_status("event123") is None

    def test_set_and_get_previous_status(self, state_tracker):
        """Test setting and getting game status."""
        state_tracker.set_previous_status("event123", 2)
        assert state_tracker.get_previous_status("event123") == 2

    def test_play_deduplication(self, state_tracker):
        """Test play deduplication."""
        assert state_tracker.has_seen_play("play_1") is False
        state_tracker.mark_play_seen("play_1")
        assert state_tracker.has_seen_play("play_1") is True

    def test_drive_deduplication(self, state_tracker):
        """Test drive deduplication."""
        assert state_tracker.has_seen_drive("drive_1") is False
        state_tracker.mark_drive_seen("drive_1")
        assert state_tracker.has_seen_drive("drive_1") is True

    def test_game_started_tracking(self, state_tracker):
        """Test game started tracking."""
        assert state_tracker.has_game_started("event123") is False
        state_tracker.mark_game_started("event123")
        assert state_tracker.has_game_started("event123") is True

    def test_game_initialized_tracking(self, state_tracker):
        """Test game initialized tracking."""
        assert state_tracker.is_game_initialized("event123") is False
        state_tracker.mark_game_initialized("event123")
        assert state_tracker.is_game_initialized("event123") is True

    def test_odds_changed_first_time(self, state_tracker):
        """Test that first odds are always considered changed."""
        odds = {"spread": 3.5, "overUnder": 45.5}
        assert state_tracker.odds_changed("event123", odds) is True

    def test_odds_changed_when_different(self, state_tracker):
        """Test that different odds are detected."""
        old_odds = {"spread": 3.5, "overUnder": 45.5}
        state_tracker.set_last_odds("event123", old_odds)

        new_odds = {"spread": 4.0, "overUnder": 45.5}
        assert state_tracker.odds_changed("event123", new_odds) is True

    def test_odds_not_changed_when_same(self, state_tracker):
        """Test that same odds are not marked as changed."""
        odds = {"spread": 3.5, "overUnder": 45.5}
        state_tracker.set_last_odds("event123", odds)
        assert state_tracker.odds_changed("event123", odds) is False

    def test_filter_new_plays(self, state_tracker):
        """Test filtering new plays."""
        plays = [
            {"id": "1", "text": "First play"},
            {"id": "2", "text": "Second play"},
            {"id": "3", "text": "Third play"},
        ]

        # First call should return all plays
        new_plays = state_tracker.filter_new_plays("event123", plays)
        assert len(new_plays) == 3

        # Second call should return empty
        new_plays = state_tracker.filter_new_plays("event123", plays)
        assert len(new_plays) == 0

        # Adding new play should return only that one
        plays.append({"id": "4", "text": "Fourth play"})
        new_plays = state_tracker.filter_new_plays("event123", plays)
        assert len(new_plays) == 1
        assert new_plays[0]["id"] == "4"

    def test_filter_new_drives(self, state_tracker):
        """Test filtering new drives."""
        drives = [
            {"id": "1", "result": "Touchdown"},
            {"id": "2", "result": "Punt"},
        ]

        # First call should return all completed drives
        new_drives = state_tracker.filter_new_drives("event123", drives)
        assert len(new_drives) == 2

        # Second call should return empty
        new_drives = state_tracker.filter_new_drives("event123", drives)
        assert len(new_drives) == 0

    def test_filter_new_drives_ignores_incomplete(self, state_tracker):
        """Test that incomplete drives are ignored."""
        drives = [
            {"id": "1", "result": "Touchdown"},
            {"id": "2"},  # No result = incomplete drive
        ]

        new_drives = state_tracker.filter_new_drives("event123", drives)
        assert len(new_drives) == 1
        assert new_drives[0]["id"] == "1"


# =============================================================================
# Event Tests
# =============================================================================


class TestNFLEvents:
    """Tests for NFL event dataclasses."""

    def test_game_initialize_event(self):
        """Test NFLGameInitializeEvent creation."""
        event = NFLGameInitializeEvent(
            event_id="401671827",
            home_team="Kansas City Chiefs",
            away_team="San Francisco 49ers",
            home_team_abbreviation="KC",
            away_team_abbreviation="SF",
            venue="Allegiant Stadium",
            week=22,
            season_type=3,
        )

        assert event.event_id == "401671827"
        assert event.home_team == "Kansas City Chiefs"
        assert event.away_team == "San Francisco 49ers"
        assert event.event_type == "nfl_game_initialize"

    def test_game_result_event(self):
        """Test NFLGameResultEvent creation."""
        event = NFLGameResultEvent(
            event_id="401671827",
            winner="home",
            final_score={"home": 25, "away": 22},
            home_team="Kansas City Chiefs",
            away_team="San Francisco 49ers",
        )

        assert event.winner == "home"
        assert event.final_score["home"] == 25
        assert event.event_type == "nfl_game_result"

    def test_play_event(self):
        """Test NFLPlayEvent creation."""
        event = NFLPlayEvent(
            event_id="401671827",
            play_id="12345",
            sequence_number=100,
            quarter=3,
            game_clock="12:34",
            down=2,
            distance=7,
            yard_line=35,
            play_type="Pass",
            description="P.Mahomes pass to T.Kelce for 15 yards",
            yards_gained=15,
            is_scoring_play=False,
            team_abbreviation="KC",
        )

        assert event.play_type == "Pass"
        assert event.yards_gained == 15
        assert event.event_type == "nfl_play"

    def test_drive_event(self):
        """Test NFLDriveEvent creation."""
        event = NFLDriveEvent(
            event_id="401671827",
            drive_id="1",
            drive_number=5,
            team_abbreviation="KC",
            start_quarter=2,
            start_clock="8:45",
            start_yard_line=25,
            end_quarter=2,
            end_clock="4:32",
            end_yard_line=100,
            plays=8,
            yards=75,
            time_elapsed="4:13",
            result="Touchdown",
            is_score=True,
            points_scored=7,
        )

        assert event.result == "Touchdown"
        assert event.is_score is True
        assert event.event_type == "nfl_drive"

    def test_odds_update_event(self):
        """Test NFLOddsUpdateEvent creation."""
        event = NFLOddsUpdateEvent(
            event_id="401671827",
            provider="Draft Kings",
            spread=-3.5,
            over_under=47.5,
            moneyline_home=-150,
            moneyline_away=+130,
            home_team="Kansas City Chiefs",
            away_team="San Francisco 49ers",
        )

        assert event.spread == -3.5
        assert event.over_under == 47.5
        assert event.event_type == "nfl_odds_update"


# =============================================================================
# Store Parsing Tests
# =============================================================================


class TestNFLStoreParseScoreboard:
    """Tests for NFLStore scoreboard parsing."""

    def test_parse_scoreboard_emits_game_initialize(self, nfl_store):
        """Test that scoreboard parsing emits GameInitializeEvent."""
        scoreboard_data = {
            "scoreboard": {
                "events": [
                    {
                        "id": "401671827",
                        "date": "2024-02-11T23:30Z",
                        "competitions": [
                            {
                                "competitors": [
                                    {
                                        "homeAway": "home",
                                        "team": {
                                            "id": "12",
                                            "displayName": "Kansas City Chiefs",
                                            "abbreviation": "KC",
                                        },
                                    },
                                    {
                                        "homeAway": "away",
                                        "team": {
                                            "id": "25",
                                            "displayName": "San Francisco 49ers",
                                            "abbreviation": "SF",
                                        },
                                    },
                                ],
                                "venue": {"fullName": "Allegiant Stadium"},
                                "status": {"type": {"name": "STATUS_SCHEDULED"}},
                            }
                        ],
                    }
                ]
            }
        }

        events = nfl_store._parse_api_response(scoreboard_data)

        # Should emit GameInitializeEvent
        init_events = [e for e in events if isinstance(e, NFLGameInitializeEvent)]
        assert len(init_events) == 1
        assert init_events[0].home_team == "Kansas City Chiefs"
        assert init_events[0].away_team == "San Francisco 49ers"

    def test_parse_scoreboard_emits_odds_update(self, nfl_store):
        """Test that scoreboard parsing emits NFLOddsUpdateEvent from ESPN sportsbook data."""
        scoreboard_data = {
            "scoreboard": {
                "events": [
                    {
                        "id": "401671827",
                        "competitions": [
                            {
                                "competitors": [
                                    {
                                        "homeAway": "home",
                                        "team": {"id": "12", "displayName": "KC"},
                                    },
                                    {
                                        "homeAway": "away",
                                        "team": {"id": "25", "displayName": "SF"},
                                    },
                                ],
                                "odds": [
                                    {
                                        "provider": {"name": "Draft Kings"},
                                        "spread": -2.5,
                                        "overUnder": 47.5,
                                        "homeTeamOdds": {"moneyLine": -130},
                                        "awayTeamOdds": {"moneyLine": +110},
                                    }
                                ],
                                "status": {"type": {"name": "STATUS_SCHEDULED"}},
                            }
                        ],
                    }
                ]
            }
        }

        events = nfl_store._parse_api_response(scoreboard_data)

        # Should emit NFLOddsUpdateEvent from ESPN sportsbook data
        odds_events = [e for e in events if isinstance(e, NFLOddsUpdateEvent)]
        assert len(odds_events) == 1
        assert odds_events[0].spread == -2.5
        assert odds_events[0].over_under == 47.5

    def test_parse_scoreboard_emits_game_result(self, nfl_store):
        """Test that scoreboard parsing emits GameResultEvent for finished games."""
        # First set game to in_progress
        nfl_store._state.set_previous_status("401671827", 2)
        nfl_store._state.mark_game_initialized("401671827")

        scoreboard_data = {
            "scoreboard": {
                "events": [
                    {
                        "id": "401671827",
                        "competitions": [
                            {
                                "competitors": [
                                    {
                                        "homeAway": "home",
                                        "score": "25",
                                        "team": {"id": "12", "displayName": "KC"},
                                    },
                                    {
                                        "homeAway": "away",
                                        "score": "22",
                                        "team": {"id": "25", "displayName": "SF"},
                                    },
                                ],
                                "status": {"type": {"name": "STATUS_FINAL"}},
                            }
                        ],
                    }
                ]
            }
        }

        events = nfl_store._parse_api_response(scoreboard_data)

        # Should emit GameResultEvent
        result_events = [e for e in events if isinstance(e, NFLGameResultEvent)]
        assert len(result_events) == 1
        assert result_events[0].winner == "home"
        assert result_events[0].final_score["home"] == 25


class TestNFLStoreParsePlayByPlay:
    """Tests for NFLStore play-by-play parsing."""

    def test_parse_plays_emits_play_events(self, nfl_store):
        """Test that plays parsing emits PlayEvents."""
        plays_data = {
            "plays": {
                "eventId": "401671827",
                "items": [
                    {
                        "id": "1",
                        "sequenceNumber": 1,
                        "type": {"text": "Kickoff"},
                        "text": "Kickoff for 65 yards",
                        "period": {"number": 1},
                        "clock": {"displayValue": "15:00"},
                    },
                    {
                        "id": "2",
                        "sequenceNumber": 2,
                        "type": {"text": "Rush"},
                        "text": "I.Pacheco rush for 5 yards",
                        "period": {"number": 1},
                        "clock": {"displayValue": "14:45"},
                        "start": {"down": 1, "distance": 10, "yardLine": 25},
                        "statYardage": 5,
                    },
                ],
            }
        }

        events = nfl_store._parse_api_response(plays_data)

        # Should emit PlayEvents
        play_events = [e for e in events if isinstance(e, NFLPlayEvent)]
        assert len(play_events) == 2
        assert play_events[0].play_type == "Kickoff"
        assert play_events[1].play_type == "Rush"
        assert play_events[1].yards_gained == 5

    def test_parse_plays_emits_game_start_on_first_play(self, nfl_store):
        """Test that first play triggers GameStartEvent."""
        plays_data = {
            "plays": {
                "eventId": "401671827",
                "items": [
                    {
                        "id": "1",
                        "type": {"text": "Kickoff"},
                        "text": "Opening kickoff",
                        "period": {"number": 1},
                        "clock": {"displayValue": "15:00"},
                    }
                ],
            }
        }

        events = nfl_store._parse_api_response(plays_data)

        # Should emit GameStartEvent
        start_events = [e for e in events if isinstance(e, NFLGameStartEvent)]
        assert len(start_events) == 1

    def test_parse_plays_deduplication(self, nfl_store):
        """Test that duplicate plays are not emitted."""
        plays_data = {
            "plays": {
                "eventId": "401671827",
                "items": [
                    {
                        "id": "1",
                        "type": {"text": "Kickoff"},
                        "text": "Opening kickoff",
                    }
                ],
            }
        }

        # First parse
        events1 = nfl_store._parse_api_response(plays_data)
        play_events1 = [e for e in events1 if isinstance(e, NFLPlayEvent)]
        assert len(play_events1) == 1

        # Second parse with same data
        events2 = nfl_store._parse_api_response(plays_data)
        play_events2 = [e for e in events2 if isinstance(e, NFLPlayEvent)]
        assert len(play_events2) == 0  # Deduplicated


# =============================================================================
# Utility Function Tests
# =============================================================================


class TestNFLUtils:
    """Tests for NFL utility functions."""

    def test_get_team_abbreviation(self):
        """Test team ID to abbreviation conversion."""
        assert get_team_abbreviation("12") == "KC"
        assert get_team_abbreviation("25") == "SF"
        assert get_team_abbreviation("999") == ""

    def test_get_team_name(self):
        """Test abbreviation to team name conversion."""
        assert get_team_name("KC") == "Kansas City Chiefs"
        assert get_team_name("SF") == "San Francisco 49ers"
        assert get_team_name("XXX") == ""

    def test_get_team_division(self):
        """Test team division lookup."""
        assert get_team_division("KC") == "AFC West"
        assert get_team_division("SF") == "NFC West"
        assert get_team_division("XXX") == ""

    def test_parse_iso_datetime(self):
        """Test ISO datetime parsing."""
        # With Z suffix
        dt = parse_iso_datetime("2024-02-11T23:30:00Z")
        assert dt.tzinfo is not None
        assert dt.year == 2024

        # With offset
        dt = parse_iso_datetime("2024-02-11T18:30:00-05:00")
        assert dt.tzinfo is not None

    def test_format_game_clock(self):
        """Test game clock formatting."""
        assert format_game_clock(900) == "15:00"
        assert format_game_clock(754) == "12:34"
        assert format_game_clock(0) == "0:00"
        assert format_game_clock(-10) == "0:00"

    def test_american_odds_to_probability(self):
        """Test American odds to probability conversion."""
        # Favorites (negative odds)
        prob = american_odds_to_probability(-110)
        assert 0.52 < prob < 0.53

        # Underdogs (positive odds)
        prob = american_odds_to_probability(200)
        assert 0.33 < prob < 0.34

        # Even money
        prob = american_odds_to_probability(0)
        assert prob == 0.5

    def test_probability_to_american_odds(self):
        """Test probability to American odds conversion."""
        # Favorite
        odds = probability_to_american_odds(0.6)
        assert odds < 0  # Negative for favorites

        # Underdog
        odds = probability_to_american_odds(0.4)
        assert odds > 0  # Positive for underdogs

    def test_spread_to_favorite(self):
        """Test spread to favorite team determination."""
        assert spread_to_favorite(3.5, "KC", "SF") == "KC"
        assert spread_to_favorite(-3.5, "KC", "SF") == "SF"
        assert spread_to_favorite(0, "KC", "SF") == "Pick"


# =============================================================================
# Integration Tests (Marked for separate run)
# =============================================================================


@pytest.mark.integration
class TestNFLAPIIntegration:
    """Integration tests for NFL ESPN API."""

    @pytest.mark.asyncio
    async def test_fetch_scoreboard(self):
        """Test fetching live scoreboard data."""
        api = NFLExternalAPI()
        try:
            data = await api.fetch("scoreboard")
            assert "scoreboard" in data
            assert "events" in data["scoreboard"] or "leagues" in data["scoreboard"]
        finally:
            await api.close()

    @pytest.mark.asyncio
    async def test_fetch_teams(self):
        """Test fetching teams data."""
        api = NFLExternalAPI()
        try:
            data = await api.fetch("teams")
            assert "teams" in data
            assert len(data["teams"]) == 32  # 32 NFL teams
        finally:
            await api.close()
