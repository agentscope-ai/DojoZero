"""Tests for World Cup scheduling: GameFetcher + backtest replays.

The replay tests drive the full ``WorldCupStore`` pipeline against captured
ESPN responses for two completed 2025 FIFA Club World Cup matches:

- **Chelsea 3 - 0 PSG** (event 735958, July 13 final) — clean regulation
  finish, 191 plays. Validates the happy path end-to-end.
- **Al Hilal 4 - 3 Man City** (event 735949, July 1 quarterfinal) — 264
  plays, ``STATUS_FINAL_AET``. Validates the AET status mapping fix.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from dojozero.data._game_info import GameInfo
from dojozero.data._models import (
    GameInitializeEvent,
    GameResultEvent,
)
from dojozero.data.world_cup import (
    WorldCupPlayEvent,
    WorldCupStore,
)
from dojozero.dashboard_server._game_discovery import (
    WorldCupGameFetcher,
    _parse_espn_soccer_scoreboard,
)


FIXTURES = Path(__file__).parent / "fixtures" / "world_cup" / "cwc_2025"


def _load(name: str) -> dict[str, Any]:
    with open(FIXTURES / name) as fp:
        return json.load(fp)


# =============================================================================
# Backtest replays
# =============================================================================


class TestChelseaPSGFinalReplay:
    """End-to-end replay of the CWC 2025 final."""

    @pytest.fixture
    def store(self) -> WorldCupStore:
        s = WorldCupStore(store_id="cwc_final", league="fifa.cwc")
        s.set_poll_identifier(
            {
                "espn_game_id": "735958",
                "game_date": "2025-07-13",
                "league": "fifa.cwc",
            }
        )
        return s

    def test_summary_then_plays_full_replay(self, store: WorldCupStore) -> None:
        summary = _load("summary_735958_final.json")
        plays = _load("plays_735958_final.json")

        events_from_summary = list(store._parse_api_response({"summary": summary}))
        events_from_plays = list(store._parse_api_response({"plays": plays}))

        # Summary call: 1 GameInitialize + 1 GameUpdate
        s_counts = Counter(type(e).__name__ for e in events_from_summary)
        assert s_counts == {
            "GameInitializeEvent": 1,
            "WorldCupGameUpdateEvent": 1,
        }

        # Plays call: 1 GameStart + 191 PlayEvents + 1 GameResult
        p_counts = Counter(type(e).__name__ for e in events_from_plays)
        assert p_counts == {
            "GameStartEvent": 1,
            "WorldCupPlayEvent": 191,
            "GameResultEvent": 1,
        }

        # Game result correctness
        results = [e for e in events_from_plays if isinstance(e, GameResultEvent)]
        r = results[0]
        assert r.winner == "home"
        assert r.home_team_name == "Chelsea"
        assert r.away_team_name == "Paris Saint-Germain"
        assert r.home_score == 3
        assert r.away_score == 0

        # GameInitialize carries venue/teams from the summary
        inits = [e for e in events_from_summary if isinstance(e, GameInitializeEvent)]
        assert len(inits) == 1
        init = inits[0]
        # TeamIdentity object (not a string) once the summary is rich enough
        assert "Chelsea" in str(init.home_team)
        assert "Paris" in str(init.away_team)

    def test_re_polling_plays_yields_no_duplicate_events(
        self, store: WorldCupStore
    ) -> None:
        summary = _load("summary_735958_final.json")
        plays = _load("plays_735958_final.json")
        list(store._parse_api_response({"summary": summary}))
        list(store._parse_api_response({"plays": plays}))

        # Second pass: dedup should suppress all play events and one-shot
        # lifecycle events.
        events = list(store._parse_api_response({"plays": plays}))
        counts = Counter(type(e).__name__ for e in events)
        assert counts.get("WorldCupPlayEvent", 0) == 0
        assert counts.get("GameStartEvent", 0) == 0
        assert counts.get("GameResultEvent", 0) == 0


class TestAlHilalManCityAETReplay:
    """End-to-end replay of a CWC 2025 QF that went to extra time."""

    @pytest.fixture
    def store(self) -> WorldCupStore:
        s = WorldCupStore(store_id="cwc_aet", league="fifa.cwc")
        s.set_poll_identifier(
            {
                "espn_game_id": "735949",
                "game_date": "2025-07-01",
                "league": "fifa.cwc",
            }
        )
        return s

    def test_aet_status_maps_to_final(self, store: WorldCupStore) -> None:
        summary = _load("summary_735949_aet.json")
        list(store._parse_api_response({"summary": summary}))
        # The summary's STATUS_FINAL_AET should map to STATUS_FINAL (3).
        assert store._state.get_previous_status("735949") == store._state.STATUS_FINAL

    def test_aet_match_emits_full_play_stream_and_result(
        self, store: WorldCupStore
    ) -> None:
        summary = _load("summary_735949_aet.json")
        plays = _load("plays_735949_aet.json")
        list(store._parse_api_response({"summary": summary}))
        events = list(store._parse_api_response({"plays": plays}))
        counts = Counter(type(e).__name__ for e in events)
        assert counts == {
            "GameStartEvent": 1,
            "WorldCupPlayEvent": 264,
            "GameResultEvent": 1,
        }

        result = next(e for e in events if isinstance(e, GameResultEvent))
        # Al Hilal won 4-3 (away team in ESPN's listing).
        assert result.winner == "away"
        assert result.home_team_name == "Manchester City"
        assert result.away_team_name == "Al Hilal"
        assert result.home_score == 3
        assert result.away_score == 4

    def test_aet_match_has_period_three_plays(self, store: WorldCupStore) -> None:
        plays = _load("plays_735949_aet.json")
        events = list(store._parse_api_response({"plays": plays}))
        play_events = [e for e in events if isinstance(e, WorldCupPlayEvent)]
        et_plays = [p for p in play_events if p.period >= 3]
        assert et_plays, "expected at least one extra-time play (period >= 3)"


# =============================================================================
# WorldCupGameFetcher (scoreboard parsing)
# =============================================================================


class TestWorldCupGameFetcher:
    def test_default_league(self) -> None:
        fetcher = WorldCupGameFetcher()
        assert fetcher.league == "fifa.world"

    def test_custom_league(self) -> None:
        assert WorldCupGameFetcher(league="fifa.cwc").league == "fifa.cwc"

    def test_invalid_league_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown FIFA league code"):
            WorldCupGameFetcher(league="fifa.world/../../nfl")

    def test_parse_scoreboard_maps_status_canonical(self) -> None:
        """A completed CWC final scoreboard fixture should yield status=3."""
        raw = _load("scoreboard_20250713.json")
        # Mimic ESPNExternalAPI.fetch's wrapper:
        games = _parse_espn_soccer_scoreboard({"scoreboard": raw})
        assert len(games) == 1
        g = games[0]
        assert g.game_id == "735958"
        assert g.status == 3  # canonical FINAL, not raw ESPN id (28)
        assert "Full Time" in g.status_text

    def test_parse_scoreboard_maps_scheduled_canonical(self) -> None:
        """The fifa.world fixture has Jun 2026 matches still scheduled."""
        # Reuse the top-level scoreboard fixture captured during P0
        path = (
            Path(__file__).parent
            / "fixtures"
            / "world_cup"
            / "scoreboard_fifa_world.json"
        )
        raw = json.load(open(path))
        games = _parse_espn_soccer_scoreboard({"scoreboard": raw})
        assert games, "expected at least one event"
        for g in games:
            assert g.status == 1  # canonical SCHEDULED
            assert g.game_time_utc is not None

    @pytest.mark.asyncio
    async def test_fetch_games_for_date_range_aggregates_days(
        self, monkeypatch
    ) -> None:
        fetcher = WorldCupGameFetcher(league="fifa.cwc")
        calls: list[tuple[object, str]] = []
        closed = False

        class FakeApi:
            async def close(self) -> None:
                nonlocal closed
                closed = True

        fake_api = FakeApi()

        async def fake_fetch(api_arg: object, date: str) -> list[GameInfo]:
            calls.append((api_arg, date))
            return [
                GameInfo.model_validate(
                    {
                        "gameId": f"game-{date}",
                        "sport_type": "world_cup",
                        "homeTeam": {"displayName": "Home"},
                        "awayTeam": {"displayName": "Away"},
                    }
                )
            ]

        monkeypatch.setattr(fetcher, "_make_api", lambda: fake_api)
        monkeypatch.setattr(fetcher, "_fetch_games_for_api_date", fake_fetch)

        games = await fetcher.fetch_games_for_date_range("2025-07-01", "2025-07-03")

        assert calls == [
            (fake_api, "2025-07-01"),
            (fake_api, "2025-07-02"),
            (fake_api, "2025-07-03"),
        ]
        assert closed is True
        assert [g.game_id for g in games] == [
            "game-2025-07-01",
            "game-2025-07-02",
            "game-2025-07-03",
        ]

    @pytest.mark.asyncio
    async def test_get_game_status_info_returns_matching_game(
        self, monkeypatch
    ) -> None:
        fetcher = WorldCupGameFetcher(league="fifa.cwc")
        calls: list[str | None] = []

        async def fake_fetch(date: str | None = None) -> list[GameInfo]:
            calls.append(date)
            return [
                GameInfo.model_validate(
                    {
                        "gameId": "other",
                        "sport_type": "world_cup",
                        "gameStatus": 1,
                        "gameStatusText": "Scheduled",
                        "homeTeam": {"displayName": "Home"},
                        "awayTeam": {"displayName": "Away"},
                    }
                ),
                GameInfo.model_validate(
                    {
                        "gameId": "target",
                        "sport_type": "world_cup",
                        "gameStatus": 3,
                        "gameStatusText": "Full Time",
                        "homeTeam": {"displayName": "Home"},
                        "awayTeam": {"displayName": "Away"},
                    }
                ),
            ]

        monkeypatch.setattr(fetcher, "fetch_games_for_date", fake_fetch)

        assert await fetcher.get_game_status_info("target", "2025-07-13") == (
            3,
            "Full Time",
        )
        assert calls == ["2025-07-13"]

    @pytest.mark.asyncio
    async def test_get_game_status_info_returns_none_when_absent(
        self, monkeypatch
    ) -> None:
        fetcher = WorldCupGameFetcher(league="fifa.cwc")

        async def fake_fetch(date: str | None = None) -> list[GameInfo]:
            return []

        monkeypatch.setattr(fetcher, "fetch_games_for_date", fake_fetch)

        assert await fetcher.get_game_status_info("missing", "2025-07-13") is None


# =============================================================================
# Trial source YAML loads (compact format with league override)
# =============================================================================


class TestTrialSourceYAML:
    def test_world_cup_daily_yaml_loads_with_league_override(self) -> None:
        from dojozero.cli import _load_trial_source_from_yaml

        repo_root = Path(__file__).parent.parent.parent.parent
        path = repo_root / "trial_sources" / "daily" / "world_cup.yaml"
        data = _load_trial_source_from_yaml(path)
        assert data["sport_type"] == "world_cup"
        cfg: dict[str, Any] = dict(data["config"])
        assert cfg["scenario_name"] == "world_cup"
        scenario_config: dict[str, Any] = cfg["scenario_config"]
        # Daily uses fifa.cwc for backtest validation
        assert scenario_config["league"] == "fifa.cwc"
        # Schedule defaults flowed from base
        assert cfg["pre_start_hours"] == 2.0
        assert cfg["sync_interval_seconds"] == 3600.0
        stream_ids = {s["id"] for s in scenario_config["data_streams"]}
        assert "odds_update_stream" in stream_ids
        assert scenario_config["operators"][0]["class"] == "BrokerOperator"
        assert "odds_update_stream" in scenario_config["operators"][0]["data_streams"]

    def test_world_cup_prod_yaml_uses_fifa_world(self) -> None:
        from dojozero.cli import _load_trial_source_from_yaml

        repo_root = Path(__file__).parent.parent.parent.parent
        path = repo_root / "trial_sources" / "prod" / "world_cup.yaml"
        data = _load_trial_source_from_yaml(path)
        cfg: dict[str, Any] = dict(data["config"])
        scenario_config: dict[str, Any] = cfg["scenario_config"]
        assert scenario_config["league"] == "fifa.world"
        # Prod runs the full persona × LLM matrix
        agents = scenario_config["agents"]
        personas = {a["persona"] for a in agents}
        assert personas == {"degen", "mystic", "pundit", "shark", "sheep", "whale"}

    def test_world_cup_prediction_daily_yaml_loads(self) -> None:
        from dojozero.cli import _load_trial_source_from_yaml

        repo_root = Path(__file__).parent.parent.parent.parent
        path = repo_root / "trial_sources" / "daily" / "world_cup_prediction.yaml"
        data = _load_trial_source_from_yaml(path)
        assert data["source_id"] == "world-cup-prediction-source"
        assert data["sport_type"] == "world_cup"
        cfg: dict[str, Any] = dict(data["config"])
        scenario_config: dict[str, Any] = cfg["scenario_config"]
        assert scenario_config["league"] == "fifa.cwc"
        operators = scenario_config["operators"]
        assert operators[0]["class"] == "PredictionBroker"
        assert operators[0]["data_streams"] == [
            "game_lifecycle_stream",
            "game_update_stream",
        ]

    def test_world_cup_prediction_prod_yaml_uses_fifa_world(self) -> None:
        from dojozero.cli import _load_trial_source_from_yaml

        repo_root = Path(__file__).parent.parent.parent.parent
        path = repo_root / "trial_sources" / "prod" / "world_cup_prediction.yaml"
        data = _load_trial_source_from_yaml(path)
        cfg: dict[str, Any] = dict(data["config"])
        scenario_config: dict[str, Any] = cfg["scenario_config"]
        assert scenario_config["league"] == "fifa.world"
        assert scenario_config["operators"][0]["class"] == "PredictionBroker"
        personas = {a["persona"] for a in scenario_config["agents"]}
        assert personas == {"degen", "mystic", "pundit", "shark", "sheep", "whale"}

    def test_world_cup_client_yaml_has_no_built_in_agents(self) -> None:
        from dojozero.cli import _load_trial_source_from_yaml

        repo_root = Path(__file__).parent.parent.parent.parent
        path = repo_root / "trial_sources" / "client" / "world_cup.yaml"
        data = _load_trial_source_from_yaml(path)
        assert data["source_id"] == "world-cup-client-moneyline-source"
        cfg: dict[str, Any] = dict(data["config"])
        scenario_config: dict[str, Any] = cfg["scenario_config"]
        assert scenario_config["league"] == "fifa.world"
        assert scenario_config["agents"] == []
        assert scenario_config["operators"][0]["class"] == "BrokerOperator"

    def test_world_cup_prediction_client_yaml_has_no_built_in_agents(self) -> None:
        from dojozero.cli import _load_trial_source_from_yaml

        repo_root = Path(__file__).parent.parent.parent.parent
        path = repo_root / "trial_sources" / "client" / "world_cup_prediction.yaml"
        data = _load_trial_source_from_yaml(path)
        assert data["source_id"] == "world-cup-client-prediction-source"
        cfg: dict[str, Any] = dict(data["config"])
        scenario_config: dict[str, Any] = cfg["scenario_config"]
        assert scenario_config["league"] == "fifa.world"
        assert scenario_config["agents"] == []
        assert scenario_config["operators"][0]["class"] == "PredictionBroker"

    @pytest.mark.parametrize("value", ["client", "external", "external-agent"])
    def test_client_env_tier_resolves(self, monkeypatch, value: str) -> None:
        from dojozero.cli import _resolve_env_tier

        monkeypatch.setenv("DOJOZERO_ENV", value)
        assert _resolve_env_tier() == "client"


# =============================================================================
# Scheduler registration
# =============================================================================


class TestSchedulerWiring:
    def test_world_cup_passes_register_source_validation(self) -> None:
        from dojozero.dashboard_server._scheduler import (
            ScheduleManager,
            TrialSourceConfig,
        )

        mgr = ScheduleManager.__new__(ScheduleManager)
        mgr._sources = {}  # type: ignore[attr-defined]
        mgr._persist_sources = lambda: None  # type: ignore[attr-defined]
        cfg = TrialSourceConfig(scenario_name="world_cup")
        source = mgr.register_source(
            source_id="test-world-cup-source",
            sport_type="world_cup",
            config=cfg,
        )
        assert source.sport_type == "world_cup"

    def test_invalid_sport_type_still_rejected(self) -> None:
        from dojozero.dashboard_server._scheduler import (
            ScheduleManager,
            TrialSourceConfig,
        )

        mgr = ScheduleManager.__new__(ScheduleManager)
        mgr._sources = {}  # type: ignore[attr-defined]
        mgr._persist_sources = lambda: None  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="Invalid sport_type"):
            mgr.register_source(
                source_id="bad",
                sport_type="cricket",
                config=TrialSourceConfig(scenario_name="cricket"),
            )
