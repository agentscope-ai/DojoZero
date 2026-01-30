"""Tests for PreGameStatsEvent and stats value objects."""

from dojozero.data import deserialize_data_event
from dojozero.data._models import (
    EventTypes,
    HomeAwaySplits,
    PreGameInsightEvent,
    ScheduleDensity,
    SeasonSeries,
    StatsInsightEvent,
    TeamPlayerStats,
    TeamRecentForm,
    TeamSeasonStats,
    TeamStandings,
)
from dojozero.data.espn._stats_events import PreGameStatsEvent


# ---------------------------------------------------------------------------
# Instantiation with defaults
# ---------------------------------------------------------------------------


class TestDefaultInstantiation:
    """PreGameStatsEvent can be created with no arguments (all sections None)."""

    def test_empty_event(self):
        e = PreGameStatsEvent()
        assert e.season_series is None
        assert e.home_recent_form is None
        assert e.away_recent_form is None
        assert e.home_schedule is None
        assert e.away_schedule is None
        assert e.home_team_stats is None
        assert e.away_team_stats is None
        assert e.home_splits is None
        assert e.away_splits is None
        assert e.home_players is None
        assert e.away_players is None
        assert e.home_standings is None
        assert e.away_standings is None


# ---------------------------------------------------------------------------
# Explicit fields
# ---------------------------------------------------------------------------


class TestExplicitFields:
    """PreGameStatsEvent populated with real data."""

    def test_full_event(self):
        e = PreGameStatsEvent(
            game_id="401584700",
            sport="nba",
            source="espn_stats",
            home_team_id="13",
            away_team_id="25",
            season_year=2025,
            season_type="regular",
            season_series=SeasonSeries(
                total_games=3,
                home_wins=2,
                away_wins=1,
                games=[{"date": "2025-01-10", "winner": "home"}],
            ),
            home_recent_form=TeamRecentForm(
                team_id="13",
                team_name="Lakers",
                last_n=10,
                wins=7,
                losses=3,
                streak="W3",
                avg_points_scored=112.5,
                avg_points_allowed=105.2,
            ),
            away_recent_form=TeamRecentForm(
                team_id="25",
                team_name="Thunder",
                last_n=10,
                wins=8,
                losses=2,
                streak="W5",
            ),
            home_schedule=ScheduleDensity(
                team_id="13",
                team_name="Lakers",
                days_rest=2,
                is_back_to_back=False,
                games_last_7_days=3,
                games_last_14_days=6,
            ),
            home_team_stats=TeamSeasonStats(
                team_id="13",
                team_name="Lakers",
                stats={"pointsPerGame": 115.2, "reboundsPerGame": 45.3},
                rank={"pointsPerGame": 5, "reboundsPerGame": 12},
            ),
            home_splits=HomeAwaySplits(
                team_id="13",
                team_name="Lakers",
                home_record="21-5",
                away_record="15-11",
            ),
            home_players=TeamPlayerStats(
                team_id="13",
                team_name="Lakers",
                players=[{"name": "LeBron James", "ppg": 25.3}],
            ),
            home_standings=TeamStandings(
                team_id="13",
                team_name="Lakers",
                conference="Western",
                conference_rank=3,
                division="Pacific",
                division_rank=1,
                overall_record="36-20",
            ),
        )
        assert e.season_series is not None
        assert e.season_series.total_games == 3
        assert e.home_recent_form is not None
        assert e.home_recent_form.wins == 7
        assert e.home_schedule is not None
        assert e.home_schedule.days_rest == 2
        assert not e.home_schedule.is_back_to_back
        assert e.home_team_stats is not None
        assert e.home_team_stats.stats["pointsPerGame"] == 115.2
        assert e.home_splits is not None
        assert e.home_splits.home_record == "21-5"
        assert e.home_players is not None
        assert len(e.home_players.players) == 1
        assert e.home_standings is not None
        assert e.home_standings.conference_rank == 3
        # Sections not provided should be None
        assert e.away_schedule is None
        assert e.away_team_stats is None
        assert e.away_splits is None
        assert e.away_players is None
        assert e.away_standings is None

    def test_partial_event(self):
        """Only some sections populated — the rest should be None."""
        e = PreGameStatsEvent(
            home_recent_form=TeamRecentForm(
                team_id="13",
                team_name="Lakers",
                wins=7,
                losses=3,
            ),
        )
        assert e.home_recent_form is not None
        assert e.home_recent_form.wins == 7
        assert e.season_series is None
        assert e.home_team_stats is None


# ---------------------------------------------------------------------------
# Event type correctness
# ---------------------------------------------------------------------------


class TestEventTypes:
    """PreGameStatsEvent has the correct event_type."""

    def test_event_type(self):
        assert PreGameStatsEvent().event_type == EventTypes.PREGAME_STATS.value

    def test_event_type_string(self):
        assert PreGameStatsEvent().event_type == "event.pregame_stats"


# ---------------------------------------------------------------------------
# Inheritance chain
# ---------------------------------------------------------------------------


class TestInheritance:
    """Verify MRO: PreGameStatsEvent -> StatsInsightEvent -> PreGameInsightEvent."""

    def test_is_stats_insight(self):
        assert isinstance(PreGameStatsEvent(), StatsInsightEvent)

    def test_is_pre_game_insight(self):
        assert isinstance(PreGameStatsEvent(), PreGameInsightEvent)


# ---------------------------------------------------------------------------
# Round-trip serialization: to_dict -> from_dict
# ---------------------------------------------------------------------------


class TestSerialization:
    """to_dict / from_dict round-trip."""

    def _round_trip(self, event):
        d = event.to_dict()
        assert "event_type" in d
        restored = event.__class__.from_dict(d)
        for field in event.__class__.model_fields:
            if field == "timestamp":
                continue
            assert getattr(restored, field) == getattr(event, field), (
                f"Field {field} mismatch after round-trip"
            )

    def test_empty_round_trip(self):
        self._round_trip(PreGameStatsEvent())

    def test_full_round_trip(self):
        self._round_trip(
            PreGameStatsEvent(
                game_id="401584700",
                sport="nba",
                source="espn_stats",
                season_series=SeasonSeries(total_games=3, home_wins=2, away_wins=1),
                home_recent_form=TeamRecentForm(
                    team_id="13",
                    wins=7,
                    losses=3,
                    streak="W3",
                ),
                home_schedule=ScheduleDensity(
                    team_id="13",
                    days_rest=2,
                    is_back_to_back=False,
                ),
                home_team_stats=TeamSeasonStats(
                    team_id="13",
                    stats={"ppg": 115.2},
                    rank={"ppg": 5},
                ),
                home_splits=HomeAwaySplits(
                    team_id="13",
                    home_record="21-5",
                    away_record="15-11",
                ),
                home_players=TeamPlayerStats(
                    team_id="13",
                    players=[{"name": "LeBron", "ppg": 25.3}],
                ),
                home_standings=TeamStandings(
                    team_id="13",
                    conference="Western",
                    conference_rank=3,
                ),
            )
        )


# ---------------------------------------------------------------------------
# Discriminated union deserialization
# ---------------------------------------------------------------------------


class TestDeserializeDataEvent:
    """deserialize_data_event() should reconstruct PreGameStatsEvent."""

    def test_deserialize_round_trip(self):
        original = PreGameStatsEvent(
            game_id="401584700",
            sport="nba",
            source="espn_stats",
            season_series=SeasonSeries(total_games=3),
        )
        d = original.to_dict()
        restored = deserialize_data_event(d)
        assert restored is not None
        assert type(restored) is PreGameStatsEvent

    def test_deserialize_empty(self):
        original = PreGameStatsEvent()
        d = original.to_dict()
        restored = deserialize_data_event(d)
        assert restored is not None
        assert type(restored) is PreGameStatsEvent


# ---------------------------------------------------------------------------
# Value object tests
# ---------------------------------------------------------------------------


class TestValueObjects:
    """Test individual value objects."""

    def test_season_series(self):
        s = SeasonSeries(total_games=3, home_wins=2, away_wins=1)
        assert s.total_games == 3

    def test_team_recent_form(self):
        f = TeamRecentForm(wins=7, losses=3, streak="W3")
        assert f.streak == "W3"

    def test_schedule_density(self):
        d = ScheduleDensity(days_rest=1, is_back_to_back=True, games_last_7_days=4)
        assert d.is_back_to_back
        assert d.games_last_7_days == 4

    def test_team_season_stats(self):
        s = TeamSeasonStats(stats={"ppg": 115.2}, rank={"ppg": 5})
        assert s.stats["ppg"] == 115.2

    def test_home_away_splits(self):
        s = HomeAwaySplits(home_record="21-5", away_record="15-11")
        assert s.home_record == "21-5"

    def test_team_player_stats(self):
        p = TeamPlayerStats(players=[{"name": "LeBron James"}])
        assert len(p.players) == 1

    def test_team_standings(self):
        s = TeamStandings(conference="Western", conference_rank=3, games_back=2.5)
        assert s.games_back == 2.5
