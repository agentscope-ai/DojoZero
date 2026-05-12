"""Tests for World Cup (FIFA soccer) data infrastructure.

Fixtures under ``tests/fixtures/world_cup/`` are real ESPN proxy responses
captured for event 684665 (Argentina @ Ecuador, 2025-09-09 CONMEBOL qualifier,
final score Ecuador 1 - Argentina 0). They are deterministic — no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dojozero.data._factory import get_store_factory, list_store_factories
from dojozero.data._models import (
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
)
from dojozero.data.world_cup import (
    SoccerGamePlayerStats,
    SoccerPlayerMatchStats,
    SoccerTeamMatchStats,
    WorldCupExternalAPI,
    WorldCupGameUpdateEvent,
    WorldCupPlayEvent,
    WorldCupStore,
)
from dojozero.data.world_cup._api import DEFAULT_LEAGUE
from dojozero.data.world_cup._utils import (
    _build_game_info_from_summary,
    _id_from_ref,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "world_cup"


def _load_fixture(name: str) -> dict[str, Any]:
    with open(FIXTURES_DIR / name) as fp:
        return json.load(fp)


# -----------------------------------------------------------------------------
# Shared fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def summary_payload() -> dict[str, Any]:
    """ESPN summary payload for CONMEBOL qualifier event 684665."""
    return _load_fixture("summary_684665.json")


@pytest.fixture
def plays_payload() -> dict[str, Any]:
    """ESPN plays payload for event 684665 (165 plays, ends Full Time)."""
    return _load_fixture("plays_684665.json")


@pytest.fixture
def scoreboard_fifa_world() -> dict[str, Any]:
    return _load_fixture("scoreboard_fifa_world.json")


@pytest.fixture
def world_cup_store() -> WorldCupStore:
    store = WorldCupStore(store_id="t", league="fifa.worldq.conmebol")
    store.set_poll_identifier(
        {
            "espn_game_id": "684665",
            "game_date": "2025-09-09",
            "league": "fifa.worldq.conmebol",
        }
    )
    return store


# =============================================================================
# API wrapper
# =============================================================================


class TestWorldCupExternalAPI:
    def test_default_league_is_fifa_world(self) -> None:
        api = WorldCupExternalAPI()
        assert api.league == DEFAULT_LEAGUE == "fifa.world"

    def test_custom_league_is_propagated(self) -> None:
        api = WorldCupExternalAPI(league="fifa.wwc")
        assert api.league == "fifa.wwc"
        assert api._api.league == "fifa.wwc"
        assert api._api.sport == "soccer"

    @pytest.mark.asyncio
    async def test_boxscore_alias_maps_to_summary(self, monkeypatch) -> None:
        api = WorldCupExternalAPI()
        captured: dict[str, Any] = {}

        async def fake_fetch(endpoint: str, params: dict[str, Any]):
            captured["endpoint"] = endpoint
            captured["params"] = params
            return {"summary": {"eventId": params["event_id"]}}

        monkeypatch.setattr(api._api, "fetch", fake_fetch)
        result = await api.fetch("boxscore", {"game_id": "684665"})
        assert captured == {
            "endpoint": "summary",
            "params": {"event_id": "684665"},
        }
        assert result == {"summary": {"eventId": "684665"}}

    @pytest.mark.asyncio
    async def test_play_by_play_alias_maps_to_plays(self, monkeypatch) -> None:
        api = WorldCupExternalAPI()
        captured: dict[str, Any] = {}

        async def fake_fetch(endpoint: str, params: dict[str, Any]):
            captured["endpoint"] = endpoint
            captured["params"] = params
            return {"plays": {"items": [], "eventId": params["event_id"]}}

        monkeypatch.setattr(api._api, "fetch", fake_fetch)
        await api.fetch("play_by_play", {"game_id": "684665"})
        assert captured["endpoint"] == "plays"
        assert captured["params"] == {"event_id": "684665"}


# =============================================================================
# Factory registration
# =============================================================================


class TestStoreFactoryRegistration:
    def test_world_cup_factory_is_registered(self) -> None:
        assert "world_cup" in list_store_factories()
        factory = get_store_factory("world_cup")
        assert factory is not None
        assert type(factory).__name__ == "WorldCupStoreFactory"


# =============================================================================
# Event registration / discriminated union round-trip
# =============================================================================


class TestEventRegistration:
    def test_play_event_roundtrip(self) -> None:
        from dojozero.data import deserialize_data_event

        ev = WorldCupPlayEvent(
            game_id="g1",
            play_id="p1",
            sport="world_cup",
            action_type="Goal",
            action_type_id="98",
            player_id="171771",
            player_name="Enner Valencia",
            home_score=1,
            away_score=0,
            is_scoring_play=True,
        )
        restored = deserialize_data_event(ev.to_dict())
        assert isinstance(restored, WorldCupPlayEvent)
        assert restored.action_type == "Goal"
        assert restored.is_scoring_play is True
        assert restored.get_dedup_key() == "g1_play_p1"

    def test_game_update_event_roundtrip(self) -> None:
        from dojozero.data import deserialize_data_event

        ev = WorldCupGameUpdateEvent(
            game_id="g1",
            sport="world_cup",
            period=2,
            game_clock="90'+4'",
            home_score=1,
            away_score=0,
            home_team_stats=SoccerTeamMatchStats(
                team_id="209",
                team_name="Ecuador",
                team_tricode="ECU",
                score=1,
                possession_pct=42.6,
                yellow_cards=2,
            ),
        )
        restored = deserialize_data_event(ev.to_dict())
        assert isinstance(restored, WorldCupGameUpdateEvent)
        assert restored.home_team_stats.team_name == "Ecuador"
        assert restored.home_team_stats.possession_pct == pytest.approx(42.6)


# =============================================================================
# Stat models
# =============================================================================


class TestStatModels:
    def test_team_stats_defaults_are_zero(self) -> None:
        s = SoccerTeamMatchStats()
        assert s.score == 0
        assert s.possession_pct == 0.0
        assert s.yellow_cards == 0
        assert s.team_id == ""

    def test_team_stats_are_frozen(self) -> None:
        s = SoccerTeamMatchStats(team_name="Argentina")
        with pytest.raises(Exception):
            s.team_name = "Other"  # type: ignore[misc]

    def test_player_stats_defaults(self) -> None:
        p = SoccerPlayerMatchStats(name="X", goals=2)
        assert p.goals == 2
        assert p.assists == 0
        assert p.starter is False

    def test_game_player_stats_container(self) -> None:
        c = SoccerGamePlayerStats(
            home=[SoccerPlayerMatchStats(name="A")],
            away=[SoccerPlayerMatchStats(name="B")],
        )
        assert c.home[0].name == "A"
        assert c.away[0].name == "B"


# =============================================================================
# Utils
# =============================================================================


class TestIdFromRef:
    def test_extracts_trailing_id_from_ref(self) -> None:
        ref = {
            "$ref": "http://sports.core.api.espn.com/v2/sports/soccer/leagues/fifa.worldq.conmebol/seasons/2023/athletes/171771?lang=en&region=us"
        }
        assert _id_from_ref(ref) == "171771"

    def test_returns_empty_for_missing_ref(self) -> None:
        assert _id_from_ref({}) == ""
        assert _id_from_ref(None) == ""

    def test_ignores_query_string(self) -> None:
        assert _id_from_ref({"$ref": "https://x.example/teams/209?foo=bar"}) == "209"


class TestBuildGameInfoFromSummary:
    def test_extracts_team_venue_season(self, summary_payload: dict[str, Any]) -> None:
        info = _build_game_info_from_summary(summary_payload, "684665")
        assert info is not None
        # Match-specific facts (CONMEBOL Ecuador 1-0 Argentina):
        assert info.home_team.name == "Ecuador"
        assert info.away_team.name == "Argentina"
        assert info.home_team.tricode == "ECU"
        assert info.away_team.tricode == "ARG"
        assert info.venue.name.startswith("Estadio")
        assert info.venue.city == "Quito"
        assert info.season_year == 2023

    def test_returns_none_when_competitions_missing(self) -> None:
        assert _build_game_info_from_summary({}, "684665") is None
        assert _build_game_info_from_summary({"header": {}}, "684665") is None


# =============================================================================
# Store: summary parsing
# =============================================================================


class TestSummaryParsing:
    def test_summary_emits_initialize_and_update(
        self, world_cup_store: WorldCupStore, summary_payload: dict[str, Any]
    ) -> None:
        events = list(world_cup_store._parse_api_response({"summary": summary_payload}))
        types = [type(e).__name__ for e in events]
        assert "GameInitializeEvent" in types
        assert "WorldCupGameUpdateEvent" in types

    def test_summary_populates_curated_team_stats(
        self, world_cup_store: WorldCupStore, summary_payload: dict[str, Any]
    ) -> None:
        events = list(world_cup_store._parse_api_response({"summary": summary_payload}))
        updates = [e for e in events if isinstance(e, WorldCupGameUpdateEvent)]
        assert updates, "expected a WorldCupGameUpdateEvent"
        u = updates[0]
        # Ecuador (home) scored 1 with 42.6% possession in this match
        assert u.home_team_stats.team_name == "Ecuador"
        assert u.home_team_stats.team_tricode == "ECU"
        assert u.home_team_stats.score == 1
        assert u.home_team_stats.possession_pct == pytest.approx(42.6)
        assert u.home_team_stats.yellow_cards == 2
        assert u.home_team_stats.red_cards == 1

    def test_summary_populates_player_stats(
        self, world_cup_store: WorldCupStore, summary_payload: dict[str, Any]
    ) -> None:
        events = list(world_cup_store._parse_api_response({"summary": summary_payload}))
        updates = [e for e in events if isinstance(e, WorldCupGameUpdateEvent)]
        ps = updates[0].player_stats
        assert len(ps.home) > 0
        assert len(ps.away) > 0
        # At least one player should be a starter
        assert any(p.starter for p in ps.home)
        # All player IDs should be non-empty strings
        assert all(p.player_id for p in ps.home)

    def test_initialize_is_emitted_once(
        self, world_cup_store: WorldCupStore, summary_payload: dict[str, Any]
    ) -> None:
        # First call: initialize + update
        events_a = list(
            world_cup_store._parse_api_response({"summary": summary_payload})
        )
        # Second call: only update; no second initialize
        events_b = list(
            world_cup_store._parse_api_response({"summary": summary_payload})
        )
        assert sum(isinstance(e, GameInitializeEvent) for e in events_a) == 1
        assert sum(isinstance(e, GameInitializeEvent) for e in events_b) == 0


# =============================================================================
# Store: plays parsing
# =============================================================================


class TestPlaysParsing:
    def test_plays_emit_165_play_events_plus_lifecycle(
        self, world_cup_store: WorldCupStore, plays_payload: dict[str, Any]
    ) -> None:
        events = list(world_cup_store._parse_api_response({"plays": plays_payload}))
        plays = [e for e in events if isinstance(e, WorldCupPlayEvent)]
        starts = [e for e in events if isinstance(e, GameStartEvent)]
        results = [e for e in events if isinstance(e, GameResultEvent)]
        assert len(plays) == 165
        assert len(starts) == 1
        assert len(results) == 1

    def test_dedup_filters_repeated_plays(
        self, world_cup_store: WorldCupStore, plays_payload: dict[str, Any]
    ) -> None:
        first = list(world_cup_store._parse_api_response({"plays": plays_payload}))
        second = list(world_cup_store._parse_api_response({"plays": plays_payload}))
        assert sum(isinstance(e, WorldCupPlayEvent) for e in first) == 165
        assert sum(isinstance(e, WorldCupPlayEvent) for e in second) == 0
        # Lifecycle events also one-shot
        assert sum(isinstance(e, GameStartEvent) for e in second) == 0
        assert sum(isinstance(e, GameResultEvent) for e in second) == 0

    def test_summary_then_plays_emits_correct_game_result(
        self,
        world_cup_store: WorldCupStore,
        summary_payload: dict[str, Any],
        plays_payload: dict[str, Any],
    ) -> None:
        # Summary first to populate team lookup.
        list(world_cup_store._parse_api_response({"summary": summary_payload}))
        events = list(world_cup_store._parse_api_response({"plays": plays_payload}))
        results = [e for e in events if isinstance(e, GameResultEvent)]
        assert len(results) == 1
        r = results[0]
        # Ecuador 1 - 0 Argentina
        assert r.winner == "home"
        assert r.home_team_name == "Ecuador"
        assert r.away_team_name == "Argentina"
        assert r.home_score == 1
        assert r.away_score == 0

    def test_scoring_play_carries_player_and_team(
        self,
        world_cup_store: WorldCupStore,
        summary_payload: dict[str, Any],
        plays_payload: dict[str, Any],
    ) -> None:
        list(world_cup_store._parse_api_response({"summary": summary_payload}))
        events = list(world_cup_store._parse_api_response({"plays": plays_payload}))
        scoring_plays = [
            e for e in events if isinstance(e, WorldCupPlayEvent) and e.is_scoring_play
        ]
        assert len(scoring_plays) == 1
        sp = scoring_plays[0]
        # Enner Valencia penalty for Ecuador
        assert sp.player_name == "Enner Valencia"
        assert sp.team_tricode == "ECU"
        assert sp.home_score == 1
        assert sp.away_score == 0
        assert sp.action_type_id == "98"  # ESPN id for "Penalty - Scored"


# =============================================================================
# State tracker behavior
# =============================================================================


class TestStateTracker:
    def test_status_mapping_for_soccer(self, world_cup_store: WorldCupStore) -> None:
        s = world_cup_store._state
        assert s.status_name_to_code("STATUS_SCHEDULED") == s.STATUS_SCHEDULED
        assert s.status_name_to_code("STATUS_FIRST_HALF") == s.STATUS_IN_PROGRESS
        assert s.status_name_to_code("STATUS_HALFTIME") == s.STATUS_IN_PROGRESS
        assert s.status_name_to_code("STATUS_SHOOTOUT") == s.STATUS_IN_PROGRESS
        assert s.status_name_to_code("STATUS_FULL_TIME") == s.STATUS_FINAL
        assert s.status_name_to_code("STATUS_FINAL_PEN") == s.STATUS_FINAL
        # Unknown names default to scheduled
        assert s.status_name_to_code("STATUS_NONSENSE") == s.STATUS_SCHEDULED

    def test_match_clock_only_records_valid_periods(
        self, world_cup_store: WorldCupStore
    ) -> None:
        s = world_cup_store._state
        s.update_match_clock("g1", 0, "")
        assert s.get_current_period("g1") == 0
        s.update_match_clock("g1", 2, "90'+4'")
        assert s.get_current_period("g1") == 2
        assert s.get_current_clock("g1") == "90'+4'"
        # Subsequent period=0 push should not clobber valid state
        s.update_match_clock("g1", 0, "junk")
        assert s.get_current_period("g1") == 2

    def test_pbp_available_is_one_shot(
        self, world_cup_store: WorldCupStore, plays_payload: dict[str, Any]
    ) -> None:
        s = world_cup_store._state
        assert not s.is_pbp_available("684665")
        list(world_cup_store._parse_api_response({"plays": plays_payload}))
        assert s.is_pbp_available("684665")


# =============================================================================
# Scoreboard fixture sanity
# =============================================================================


class TestScoreboardFixture:
    def test_scoreboard_fifa_world_loads(
        self, scoreboard_fifa_world: dict[str, Any]
    ) -> None:
        # Sanity: the FIFA WC scoreboard fixture has a league entry naming
        # "FIFA World Cup" and at least one event.
        leagues = scoreboard_fifa_world.get("leagues", [])
        assert leagues, "expected at least one league entry"
        assert leagues[0].get("name") == "FIFA World Cup"
