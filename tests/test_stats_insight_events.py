"""Tests for StatsInsightEvent hierarchy."""

import pytest

from dojozero.data import deserialize_data_event
from dojozero.data._models import (
    EventTypes,
    PreGameInsightEvent,
    StatsInsightEvent,
)
from dojozero.data.espn._stats_events import (
    HeadToHeadEvent,
    PlayerStatsEvent,
    RecentFormEvent,
    TeamStatsEvent,
)


# ---------------------------------------------------------------------------
# Instantiation with defaults
# ---------------------------------------------------------------------------


class TestDefaultInstantiation:
    """All stats events can be created with no arguments."""

    def test_head_to_head(self):
        e = HeadToHeadEvent()
        assert e.total_games == 0
        assert e.home_wins == 0
        assert e.away_wins == 0
        assert e.games == []

    def test_team_stats(self):
        e = TeamStatsEvent()
        assert e.team_id == ""
        assert e.stats == {}
        assert e.rank == {}

    def test_player_stats(self):
        e = PlayerStatsEvent()
        assert e.team_id == ""
        assert e.players == []

    def test_recent_form(self):
        e = RecentFormEvent()
        assert e.last_n == 10
        assert e.wins == 0
        assert e.losses == 0
        assert e.streak == ""
        assert e.games == []
        assert e.avg_points_scored == 0.0


# ---------------------------------------------------------------------------
# Explicit fields
# ---------------------------------------------------------------------------


class TestExplicitFields:
    """Events populated with real data."""

    def test_head_to_head_with_data(self):
        e = HeadToHeadEvent(
            game_id="401584700",
            sport="nba",
            source="espn_stats",
            home_team_id="13",
            away_team_id="25",
            season_year=2025,
            season_type="regular",
            total_games=5,
            home_wins=3,
            away_wins=2,
            last_n_games=5,
            games=[
                {"date": "2025-01-10", "home_score": 110, "away_score": 105},
            ],
        )
        assert e.total_games == 5
        assert e.home_wins == 3
        assert len(e.games) == 1

    def test_team_stats_with_data(self):
        e = TeamStatsEvent(
            team_id="13",
            team_name="Los Angeles Lakers",
            stats={"ppg": 115.2, "rpg": 45.3},
            rank={"ppg": 5, "rpg": 12},
        )
        assert e.stats["ppg"] == 115.2
        assert e.rank["ppg"] == 5

    def test_player_stats_with_data(self):
        e = PlayerStatsEvent(
            team_id="13",
            team_name="Los Angeles Lakers",
            players=[
                {"name": "LeBron James", "ppg": 25.3, "rpg": 7.1},
            ],
        )
        assert len(e.players) == 1
        assert e.players[0]["name"] == "LeBron James"

    def test_recent_form_with_data(self):
        e = RecentFormEvent(
            team_id="13",
            team_name="Los Angeles Lakers",
            last_n=10,
            wins=7,
            losses=3,
            streak="W3",
            avg_points_scored=112.5,
            avg_points_allowed=105.2,
        )
        assert e.wins == 7
        assert e.streak == "W3"


# ---------------------------------------------------------------------------
# Event type correctness
# ---------------------------------------------------------------------------


class TestEventTypes:
    """Each event returns the correct event_type string."""

    def test_head_to_head_type(self):
        assert HeadToHeadEvent().event_type == EventTypes.HEAD_TO_HEAD.value

    def test_team_stats_type(self):
        assert TeamStatsEvent().event_type == EventTypes.TEAM_STATS.value

    def test_player_stats_type(self):
        assert PlayerStatsEvent().event_type == EventTypes.PLAYER_STATS.value

    def test_recent_form_type(self):
        assert RecentFormEvent().event_type == EventTypes.RECENT_FORM.value


# ---------------------------------------------------------------------------
# Inheritance chain
# ---------------------------------------------------------------------------


class TestInheritance:
    """Verify MRO: Concrete -> StatsInsightEvent -> PreGameInsightEvent -> SportEvent."""

    @pytest.mark.parametrize(
        "cls",
        [HeadToHeadEvent, TeamStatsEvent, PlayerStatsEvent, RecentFormEvent],
    )
    def test_is_stats_insight(self, cls):
        assert isinstance(cls(), StatsInsightEvent)

    @pytest.mark.parametrize(
        "cls",
        [HeadToHeadEvent, TeamStatsEvent, PlayerStatsEvent, RecentFormEvent],
    )
    def test_is_pre_game_insight(self, cls):
        assert isinstance(cls(), PreGameInsightEvent)


# ---------------------------------------------------------------------------
# Round-trip serialization: to_dict -> from_dict
# ---------------------------------------------------------------------------


class TestSerialization:
    """to_dict / from_dict round-trip for each event class."""

    def _round_trip(self, event):
        d = event.to_dict()
        assert "event_type" in d
        restored = event.__class__.from_dict(d)
        # Compare key fields (timestamp may differ by microsecond)
        for field in event.__class__.model_fields:
            if field == "timestamp":
                continue
            assert getattr(restored, field) == getattr(event, field), (
                f"Field {field} mismatch after round-trip"
            )

    def test_head_to_head_round_trip(self):
        self._round_trip(
            HeadToHeadEvent(total_games=5, home_wins=3, away_wins=2, last_n_games=5)
        )

    def test_team_stats_round_trip(self):
        self._round_trip(
            TeamStatsEvent(
                team_id="13",
                team_name="Lakers",
                stats={"ppg": 115.2},
                rank={"ppg": 5},
            )
        )

    def test_player_stats_round_trip(self):
        self._round_trip(
            PlayerStatsEvent(
                team_id="13",
                players=[{"name": "LeBron", "ppg": 25.3}],
            )
        )

    def test_recent_form_round_trip(self):
        self._round_trip(
            RecentFormEvent(
                team_id="13",
                wins=7,
                losses=3,
                streak="W3",
            )
        )


# ---------------------------------------------------------------------------
# Discriminated union deserialization
# ---------------------------------------------------------------------------


class TestDeserializeDataEvent:
    """deserialize_data_event() should reconstruct stats events."""

    @pytest.mark.parametrize(
        "cls",
        [HeadToHeadEvent, TeamStatsEvent, PlayerStatsEvent, RecentFormEvent],
    )
    def test_deserialize_round_trip(self, cls):
        original = cls()
        d = original.to_dict()
        restored = deserialize_data_event(d)
        assert restored is not None
        assert type(restored) is cls
