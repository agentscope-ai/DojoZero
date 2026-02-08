"""Tests for NFL data infrastructure."""

from unittest.mock import MagicMock

import pytest

from dojozero.data._models import (
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    OddsUpdateEvent,
)
from dojozero.data._models import (
    MoneylineOdds,
    OddsInfo,
    SpreadOdds,
    TeamIdentity,
    VenueInfo,
)
from dojozero.data.nfl import (
    NFLDriveEvent,
    NFLExternalAPI,
    NFLGameStateTracker,
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
# Shared Fixtures and Test Data
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


@pytest.fixture
def sample_scoreboard_data():
    """Sample scoreboard data for testing."""
    return {
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


@pytest.fixture
def sample_plays_data():
    """Sample plays data for testing."""
    return {
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
        """Test GameInitializeEvent creation."""
        event = GameInitializeEvent(
            game_id="401671827",
            home_team=TeamIdentity(name="Kansas City Chiefs", tricode="KC"),
            away_team=TeamIdentity(name="San Francisco 49ers", tricode="SF"),
            venue=VenueInfo(name="Allegiant Stadium"),
            season_type="3",
        )

        assert event.game_id == "401671827"
        assert str(event.home_team) == "Kansas City Chiefs"
        assert str(event.away_team) == "San Francisco 49ers"
        assert event.event_type == "event.game_initialize"

    def test_game_result_event(self):
        """Test GameResultEvent creation."""
        event = GameResultEvent(
            game_id="401671827",
            winner="home",
            home_score=25,
            away_score=22,
            home_team_name="Kansas City Chiefs",
            away_team_name="San Francisco 49ers",
        )

        assert event.winner == "home"
        assert event.home_score == 25
        assert event.event_type == "event.game_result"

    def test_play_event(self):
        """Test NFLPlayEvent creation."""
        event = NFLPlayEvent(
            game_id="401671827",
            play_id="12345",
            sequence_number=100,
            period=3,
            clock="12:34",
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
        assert event.event_type == "event.nfl_play"

    def test_drive_event(self):
        """Test NFLDriveEvent creation."""
        event = NFLDriveEvent(
            game_id="401671827",
            drive_id="1",
            drive_number=5,
            team_tricode="KC",
            start_period=2,
            start_clock="8:45",
            start_yard_line=25,
            end_period=2,
            end_clock="4:32",
            end_yard_line=100,
            plays_count=8,
            yards=75,
            time_elapsed="4:13",
            result="Touchdown",
            is_score=True,
            points_scored=7,
        )

        assert event.result == "Touchdown"
        assert event.is_score is True
        assert event.event_type == "event.nfl_drive"

    def test_odds_update_event(self):
        """Test OddsUpdateEvent creation."""
        event = OddsUpdateEvent(
            game_id="401671827",
            odds=OddsInfo(
                provider="polymarket",
                spreads=[SpreadOdds(spread=-3.5)],
                moneyline=MoneylineOdds(home_odds=-150.0, away_odds=130.0),
            ),
            home_tricode="KC",
            away_tricode="SF",
        )

        assert len(event.odds.spreads) > 0 and event.odds.spreads[0].spread == -3.5
        assert event.odds.provider == "polymarket"
        assert event.event_type == "event.odds_update"


# =============================================================================
# Store Parsing Tests
# =============================================================================


class TestNFLStoreParseScoreboard:
    """Tests for NFLStore scoreboard parsing."""

    def test_parse_scoreboard_emits_game_initialize(
        self, nfl_store, sample_scoreboard_data
    ):
        """Test that scoreboard parsing emits GameInitializeEvent."""
        events = nfl_store._parse_api_response(sample_scoreboard_data)

        init_events = [e for e in events if isinstance(e, GameInitializeEvent)]
        assert len(init_events) == 1
        assert str(init_events[0].home_team) == "Kansas City Chiefs"
        assert str(init_events[0].away_team) == "San Francisco 49ers"

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
        result_events = [e for e in events if isinstance(e, GameResultEvent)]
        assert len(result_events) == 1
        assert result_events[0].winner == "home"
        assert result_events[0].home_score == 25


class TestNFLStoreParsePlayByPlay:
    """Tests for NFLStore play-by-play parsing."""

    def test_parse_plays_emits_play_events(self, nfl_store, sample_plays_data):
        """Test that plays parsing emits PlayEvents."""
        events = nfl_store._parse_api_response(sample_plays_data)

        play_events = [e for e in events if isinstance(e, NFLPlayEvent)]
        assert len(play_events) == 2
        assert play_events[0].play_type == "Kickoff"
        assert play_events[1].play_type == "Rush"
        assert play_events[1].yards_gained == 5

    def test_parse_plays_emits_game_start_on_first_play(
        self, nfl_store, sample_plays_data
    ):
        """Test that first play triggers GameStartEvent."""
        events = nfl_store._parse_api_response(sample_plays_data)

        start_events = [e for e in events if isinstance(e, GameStartEvent)]
        assert len(start_events) == 1

    def test_parse_plays_deduplication(self, nfl_store, sample_plays_data):
        """Test that duplicate plays are not emitted."""
        # First parse
        events1 = nfl_store._parse_api_response(sample_plays_data)
        play_events1 = [e for e in events1 if isinstance(e, NFLPlayEvent)]
        assert len(play_events1) == 2

        # Second parse with same data
        events2 = nfl_store._parse_api_response(sample_plays_data)
        play_events2 = [e for e in events2 if isinstance(e, NFLPlayEvent)]
        assert len(play_events2) == 0  # Deduplicated


# =============================================================================
# Utility Function Tests
# =============================================================================


class TestYardLineConversion:
    """Tests for yard_line conversion from 'KC 25' format to 0-100 int."""

    def test_yard_line_home_territory(self, nfl_store):
        """Test yard_line in home team territory (e.g., 'KC 25' when KC is home)."""
        summary_data = {
            "summary": {
                "eventId": "401671827",
                "header": {
                    "id": "401671827",
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "home", "score": "7"},
                                {"homeAway": "away", "score": "3"},
                            ],
                            "status": {
                                "type": {"name": "STATUS_IN_PROGRESS"},
                                "period": 1,
                                "displayClock": "10:00",
                            },
                        }
                    ],
                },
                "boxscore": {
                    "teams": [
                        {
                            "homeAway": "home",
                            "team": {
                                "abbreviation": "KC",
                                "displayName": "Kansas City Chiefs",
                            },
                        },
                        {
                            "homeAway": "away",
                            "team": {
                                "abbreviation": "SF",
                                "displayName": "San Francisco 49ers",
                            },
                        },
                    ],
                },
                "situation": {
                    "possession": "KC",
                    "down": 2,
                    "distance": 7,
                    "yardLine": "KC 25",  # Home territory
                },
            }
        }

        # Mark game as initialized to emit updates
        nfl_store._state.mark_game_initialized("401671827")

        from dojozero.data.nfl._events import NFLGameUpdateEvent

        events = nfl_store._parse_api_response(summary_data)
        update_events = [e for e in events if isinstance(e, NFLGameUpdateEvent)]

        assert len(update_events) == 1
        # KC is home, "KC 25" means 25 yards from home goal = 25
        assert update_events[0].yard_line == 25

    def test_yard_line_away_territory(self, nfl_store):
        """Test yard_line in away team territory (e.g., 'SF 25' when KC is home)."""
        summary_data = {
            "summary": {
                "eventId": "401671828",
                "header": {
                    "id": "401671828",
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "home", "score": "14"},
                                {"homeAway": "away", "score": "10"},
                            ],
                            "status": {
                                "type": {"name": "STATUS_IN_PROGRESS"},
                                "period": 2,
                                "displayClock": "5:00",
                            },
                        }
                    ],
                },
                "boxscore": {
                    "teams": [
                        {
                            "homeAway": "home",
                            "team": {
                                "abbreviation": "KC",
                                "displayName": "Kansas City Chiefs",
                            },
                        },
                        {
                            "homeAway": "away",
                            "team": {
                                "abbreviation": "SF",
                                "displayName": "San Francisco 49ers",
                            },
                        },
                    ],
                },
                "situation": {
                    "possession": "KC",
                    "down": 1,
                    "distance": 10,
                    "yardLine": "SF 25",  # Away territory
                },
            }
        }

        nfl_store._state.mark_game_initialized("401671828")

        from dojozero.data.nfl._events import NFLGameUpdateEvent

        events = nfl_store._parse_api_response(summary_data)
        update_events = [e for e in events if isinstance(e, NFLGameUpdateEvent)]

        assert len(update_events) == 1
        # KC is home, "SF 25" means 25 yards from away goal = 100 - 25 = 75
        assert update_events[0].yard_line == 75

    def test_yard_line_midfield(self, nfl_store):
        """Test yard_line at midfield (50 yard line)."""
        summary_data = {
            "summary": {
                "eventId": "401671829",
                "header": {
                    "id": "401671829",
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "home", "score": "0"},
                                {"homeAway": "away", "score": "0"},
                            ],
                            "status": {
                                "type": {"name": "STATUS_IN_PROGRESS"},
                                "period": 1,
                                "displayClock": "12:00",
                            },
                        }
                    ],
                },
                "boxscore": {
                    "teams": [
                        {
                            "homeAway": "home",
                            "team": {
                                "abbreviation": "KC",
                                "displayName": "Kansas City Chiefs",
                            },
                        },
                        {
                            "homeAway": "away",
                            "team": {
                                "abbreviation": "SF",
                                "displayName": "San Francisco 49ers",
                            },
                        },
                    ],
                },
                "situation": {
                    "possession": "SF",
                    "down": 3,
                    "distance": 5,
                    "yardLine": "KC 50",  # Midfield (home's 50)
                },
            }
        }

        nfl_store._state.mark_game_initialized("401671829")

        from dojozero.data.nfl._events import NFLGameUpdateEvent

        events = nfl_store._parse_api_response(summary_data)
        update_events = [e for e in events if isinstance(e, NFLGameUpdateEvent)]

        assert len(update_events) == 1
        # "KC 50" = 50 yards from home goal = midfield
        assert update_events[0].yard_line == 50

    def test_yard_line_empty_string(self, nfl_store):
        """Test yard_line with empty string defaults to 0."""
        summary_data = {
            "summary": {
                "eventId": "401671830",
                "header": {
                    "id": "401671830",
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "home", "score": "0"},
                                {"homeAway": "away", "score": "0"},
                            ],
                            "status": {
                                "type": {"name": "STATUS_IN_PROGRESS"},
                                "period": 1,
                                "displayClock": "15:00",
                            },
                        }
                    ],
                },
                "boxscore": {
                    "teams": [
                        {
                            "homeAway": "home",
                            "team": {
                                "abbreviation": "KC",
                                "displayName": "Kansas City Chiefs",
                            },
                        },
                        {
                            "homeAway": "away",
                            "team": {
                                "abbreviation": "SF",
                                "displayName": "San Francisco 49ers",
                            },
                        },
                    ],
                },
                "situation": {
                    "possession": "",
                    "down": 0,
                    "distance": 0,
                    "yardLine": "",  # Empty
                },
            }
        }

        nfl_store._state.mark_game_initialized("401671830")

        from dojozero.data.nfl._events import NFLGameUpdateEvent

        events = nfl_store._parse_api_response(summary_data)
        update_events = [e for e in events if isinstance(e, NFLGameUpdateEvent)]

        assert len(update_events) == 1
        assert update_events[0].yard_line == 0

    def test_yard_line_integer_format(self, nfl_store):
        """Test yard_line when API returns integer instead of 'KC 25' format."""
        summary_data = {
            "summary": {
                "eventId": "401671831",
                "header": {
                    "id": "401671831",
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "home", "score": "7"},
                                {"homeAway": "away", "score": "3"},
                            ],
                            "status": {
                                "type": {"name": "STATUS_IN_PROGRESS"},
                                "period": 2,
                                "displayClock": "8:00",
                            },
                        }
                    ],
                },
                "boxscore": {
                    "teams": [
                        {
                            "homeAway": "home",
                            "team": {
                                "abbreviation": "KC",
                                "displayName": "Kansas City Chiefs",
                            },
                        },
                        {
                            "homeAway": "away",
                            "team": {
                                "abbreviation": "SF",
                                "displayName": "San Francisco 49ers",
                            },
                        },
                    ],
                },
                "situation": {
                    "possession": "KC",
                    "down": 2,
                    "distance": 5,
                    "yardLine": 35,  # Integer format (like PBP API)
                },
            }
        }

        nfl_store._state.mark_game_initialized("401671831")

        from dojozero.data.nfl._events import NFLGameUpdateEvent

        events = nfl_store._parse_api_response(summary_data)
        update_events = [e for e in events if isinstance(e, NFLGameUpdateEvent)]

        assert len(update_events) == 1
        # Integer format is used directly
        assert update_events[0].yard_line == 35


class TestNFLUtils:
    """Tests for NFL utility functions."""

    @pytest.mark.parametrize(
        "team_id,expected",
        [
            ("12", "KC"),
            ("25", "SF"),
            ("1", "ATL"),
            ("2", "BUF"),
            ("999", ""),
        ],
    )
    def test_get_team_abbreviation(self, team_id, expected):
        """Test team ID to abbreviation conversion."""
        assert get_team_abbreviation(team_id) == expected

    @pytest.mark.parametrize(
        "abbrev,expected",
        [
            ("KC", "Kansas City Chiefs"),
            ("SF", "San Francisco 49ers"),
            ("BUF", "Buffalo Bills"),
            ("kc", "Kansas City Chiefs"),  # Case insensitive
            ("XXX", ""),
        ],
    )
    def test_get_team_name(self, abbrev, expected):
        """Test abbreviation to team name conversion."""
        assert get_team_name(abbrev) == expected

    @pytest.mark.parametrize(
        "abbrev,expected_division",
        [
            ("KC", "AFC West"),
            ("SF", "NFC West"),
            ("BUF", "AFC East"),
            ("DAL", "NFC East"),
            ("XXX", ""),
        ],
    )
    def test_get_team_division(self, abbrev, expected_division):
        """Test team division lookup."""
        assert get_team_division(abbrev) == expected_division

    @pytest.mark.parametrize(
        "date_str,expected_year,expected_month",
        [
            ("2024-02-11T23:30:00Z", 2024, 2),
            ("2024-02-11T18:30:00-05:00", 2024, 2),
            ("2025-12-25T19:00:00Z", 2025, 12),
        ],
    )
    def test_parse_iso_datetime(self, date_str, expected_year, expected_month):
        """Test ISO datetime parsing."""
        dt = parse_iso_datetime(date_str)
        assert dt.tzinfo is not None
        assert dt.year == expected_year
        assert dt.month == expected_month

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (900, "15:00"),
            (754, "12:34"),
            (0, "0:00"),
            (-10, "0:00"),
            (60, "1:00"),
            (125, "2:05"),
        ],
    )
    def test_format_game_clock(self, seconds, expected):
        """Test game clock formatting."""
        assert format_game_clock(seconds) == expected

    @pytest.mark.parametrize(
        "odds,min_prob,max_prob",
        [
            (-110, 0.52, 0.53),
            (200, 0.33, 0.34),
            (-200, 0.66, 0.67),
            (100, 0.49, 0.51),
        ],
    )
    def test_american_odds_to_probability(self, odds, min_prob, max_prob):
        """Test American odds to probability conversion."""
        prob = american_odds_to_probability(odds)
        assert min_prob < prob < max_prob

    def test_american_odds_to_probability_even_money(self):
        """Test even money odds."""
        prob = american_odds_to_probability(0)
        assert prob == 0.5

    @pytest.mark.parametrize(
        "prob,is_negative",
        [
            (0.6, True),  # Favorite -> negative
            (0.4, False),  # Underdog -> positive
            (0.7, True),  # Strong favorite
            (0.3, False),  # Strong underdog
        ],
    )
    def test_probability_to_american_odds(self, prob, is_negative):
        """Test probability to American odds conversion."""
        odds = probability_to_american_odds(prob)
        assert (odds < 0) == is_negative

    @pytest.mark.parametrize(
        "spread,home,away,expected",
        [
            (3.5, "KC", "SF", "KC"),
            (-3.5, "KC", "SF", "SF"),
            (0, "KC", "SF", "Pick"),
            (7.0, "DAL", "NYG", "DAL"),
        ],
    )
    def test_spread_to_favorite(self, spread, home, away, expected):
        """Test spread to favorite team determination."""
        assert spread_to_favorite(spread, home, away) == expected


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
