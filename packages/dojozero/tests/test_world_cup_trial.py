"""Tests for the world_cup trial builder registration and validation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

# Force registration via the same import path the CLI uses.
import dojozero.world_cup  # noqa: F401
from dojozero.betting import TrialBrokerConfig
from dojozero.core._registry import get_trial_builder_definition
from dojozero.data._config import HubConfig
from dojozero.data._factory import get_store_factory
from dojozero.data._game_info import GameInfo, TeamInfo
from dojozero.world_cup._trial import WorldCupTrialParams


class TestTrialBuilderRegistration:
    def test_world_cup_builder_is_registered(self) -> None:
        defn = get_trial_builder_definition("world_cup")
        assert defn is not None
        assert defn.description and "World Cup" in defn.description
        assert defn.param_model is WorldCupTrialParams

    def test_store_factory_present_after_trial_import(self) -> None:
        # Importing dojozero.world_cup should also import the store-factory
        # module via _trial's noqa import, registering "world_cup".
        assert get_store_factory("world_cup") is not None


class TestParamsValidation:
    def _hub(self) -> HubConfig:
        return HubConfig(persistence_file="outputs/world_cup_events.jsonl")

    def _operators(self) -> list[TrialBrokerConfig]:
        return [
            TrialBrokerConfig.model_validate(
                {"id": "betting_broker", "class": "BrokerOperator"}
            )
        ]

    def test_minimal_valid_params(self) -> None:
        params = WorldCupTrialParams(
            espn_game_id="760415",
            hub=self._hub(),
            operators=self._operators(),
        )
        assert params.league == "fifa.world"
        assert params.hub_id == "world_cup_hub"

    @pytest.mark.parametrize(
        "league",
        [
            "fifa.world",
            "fifa.wwc",
            "fifa.cwc",
            "fifa.worldq.uefa",
            "fifa.worldq.conmebol",
        ],
    )
    def test_accepted_league_codes(self, league: str) -> None:
        params = WorldCupTrialParams(
            espn_game_id="x",
            league=league,
            hub=self._hub(),
            operators=self._operators(),
        )
        assert params.league == league

    def test_unknown_league_code_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            WorldCupTrialParams(
                espn_game_id="x",
                league="nfl",
                hub=self._hub(),
                operators=self._operators(),
            )
        # Pydantic wraps our ValueError; the message text must surface.
        assert "Unknown FIFA league code" in str(exc_info.value)

    def test_operators_are_required(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            WorldCupTrialParams.model_validate(
                {
                    "espn_game_id": "x",
                    "hub": self._hub(),
                }
            )
        assert "operators" in str(exc_info.value)
        assert "Field required" in str(exc_info.value)

    def test_example_params_validate(self) -> None:
        defn = get_trial_builder_definition("world_cup")
        assert defn is not None
        # `example_params` is just a dict; constructing the params class from
        # it must not raise (apart from KeyError-style mismatches).
        ep = defn.example_params or {}
        # `data_streams` / `operators` / `agents` shapes are validated by the
        # generic config models — only the params model is constructed here.
        WorldCupTrialParams.model_validate(
            {k: ep[k] for k in ("espn_game_id", "league", "hub", "operators")}
        )


class TestTrialSpecBuild:
    def _game_info(self) -> GameInfo:
        return GameInfo.model_validate(
            {
                "game_id": "760415",
                "sport_type": "world_cup",
                "game_time_utc": datetime(2026, 7, 19, 19, 0, tzinfo=timezone.utc),
                "home_team": TeamInfo.model_validate(
                    {"team_id": "1", "name": "Argentina", "tricode": "ARG"}
                ),
                "away_team": TeamInfo.model_validate(
                    {"team_id": "2", "name": "France", "tricode": "FRA"}
                ),
                "season_year": 2026,
                "season_type": "world",
            }
        )

    def _base_payload(
        self, operator: dict[str, Any], *, include_agents: bool = True
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "espn_game_id": "760415",
            "league": "fifa.world",
            "hub": {
                "persistence_file": "outputs/world_cup_events-{espn_game_id}.jsonl"
            },
            "data_streams": [
                {
                    "id": "game_lifecycle_stream",
                    "event_types": ["game_initialize", "game_start", "game_result"],
                },
                {
                    "id": "game_update_stream",
                    "event_types": ["world_cup_game_update"],
                },
                {"id": "odds_update_stream", "event_types": ["odds_update"]},
            ],
            "operators": [operator],
        }
        if include_agents:
            payload["agents"] = [
                {
                    "id": "agent",
                    "class": "BettingAgent",
                    "operators": [operator["id"]],
                    "data_streams": ["game_lifecycle_stream", "game_update_stream"],
                }
            ]
        return payload

    @pytest.mark.asyncio
    async def test_builds_betting_spec_without_polymarket_by_default(
        self, monkeypatch
    ) -> None:
        import dojozero.world_cup._trial as trial_module
        from dojozero.betting import BrokerOperator

        async def fake_game_info(*args: Any, **kwargs: Any) -> GameInfo:
            return self._game_info()

        monkeypatch.setattr(trial_module, "get_game_info_by_id_async", fake_game_info)

        defn = get_trial_builder_definition("world_cup")
        spec = await defn.build_async(
            "trial-1",
            self._base_payload(
                {
                    "id": "betting_broker",
                    "class": "BrokerOperator",
                    "initial_balance": "1000.00",
                    "allowed_tools": ["get_event", "place_market_bet_moneyline"],
                    "data_streams": [
                        "game_lifecycle_stream",
                        "odds_update_stream",
                        "game_update_stream",
                    ],
                }
            ),
        )

        assert spec.metadata.store_types == ("world_cup",)
        assert spec.metadata.world_cup_league == "fifa.world"
        assert spec.operators[0].actor_cls is BrokerOperator

    @pytest.mark.asyncio
    async def test_builds_betting_spec_with_polymarket_store_when_market_url_set(
        self, monkeypatch
    ) -> None:
        import dojozero.world_cup._trial as trial_module

        async def fake_game_info(*args: Any, **kwargs: Any) -> GameInfo:
            return self._game_info()

        monkeypatch.setattr(trial_module, "get_game_info_by_id_async", fake_game_info)

        payload = self._base_payload(
            {
                "id": "betting_broker",
                "class": "BrokerOperator",
                "initial_balance": "1000.00",
                "allowed_tools": ["get_event", "place_market_bet_moneyline"],
                "data_streams": [
                    "game_lifecycle_stream",
                    "odds_update_stream",
                    "game_update_stream",
                ],
            }
        )
        payload["market_url"] = (
            "https://polymarket.com/event/world-cup-fra-arg-2026-07-19"
        )

        defn = get_trial_builder_definition("world_cup")
        spec = await defn.build_async("trial-1", payload)

        assert spec.metadata.store_types == ("world_cup", "polymarket")
        assert spec.metadata.market_url == payload["market_url"]

    @pytest.mark.asyncio
    async def test_builds_betting_spec_without_built_in_agents(
        self, monkeypatch
    ) -> None:
        import dojozero.world_cup._trial as trial_module

        async def fake_game_info(*args: Any, **kwargs: Any) -> GameInfo:
            return self._game_info()

        monkeypatch.setattr(trial_module, "get_game_info_by_id_async", fake_game_info)

        defn = get_trial_builder_definition("world_cup")
        spec = await defn.build_async(
            "trial-1",
            self._base_payload(
                {
                    "id": "betting_broker",
                    "class": "BrokerOperator",
                    "initial_balance": "1000.00",
                    "allowed_tools": ["get_event", "place_market_bet_moneyline"],
                    "data_streams": [
                        "game_lifecycle_stream",
                        "odds_update_stream",
                        "game_update_stream",
                    ],
                },
                include_agents=False,
            ),
        )

        assert spec.agents == ()
        assert spec.social_board is None
        assert spec.operators[0].agent_ids == ()

    @pytest.mark.asyncio
    async def test_builds_prediction_spec(self, monkeypatch) -> None:
        import dojozero.world_cup._trial as trial_module
        from dojozero.betting import PredictionBroker

        async def fake_game_info(*args: Any, **kwargs: Any) -> GameInfo:
            return self._game_info()

        monkeypatch.setattr(trial_module, "get_game_info_by_id_async", fake_game_info)

        defn = get_trial_builder_definition("world_cup")
        spec = await defn.build_async(
            "trial-1",
            self._base_payload(
                {
                    "id": "prediction_broker",
                    "class": "PredictionBroker",
                    "window_pools": [5000, 4000, 3000, 2000, 500],
                    "allowed_tools": ["get_rules", "submit_prediction"],
                    "data_streams": ["game_lifecycle_stream", "game_update_stream"],
                }
            ),
        )

        assert spec.metadata.store_types == ("world_cup",)
        assert spec.operators[0].actor_cls is PredictionBroker
        assert spec.operators[0].config["window_pools"] == [5000, 4000, 3000, 2000, 500]

    @pytest.mark.asyncio
    async def test_builds_prediction_spec_without_built_in_agents(
        self, monkeypatch
    ) -> None:
        import dojozero.world_cup._trial as trial_module

        async def fake_game_info(*args: Any, **kwargs: Any) -> GameInfo:
            return self._game_info()

        monkeypatch.setattr(trial_module, "get_game_info_by_id_async", fake_game_info)

        defn = get_trial_builder_definition("world_cup")
        spec = await defn.build_async(
            "trial-1",
            self._base_payload(
                {
                    "id": "prediction_broker",
                    "class": "PredictionBroker",
                    "window_pools": [5000, 4000, 3000, 2000, 500],
                    "allowed_tools": ["get_rules", "submit_prediction"],
                    "data_streams": ["game_lifecycle_stream", "game_update_stream"],
                },
                include_agents=False,
            ),
        )

        assert spec.agents == ()
        assert spec.social_board is None
        assert spec.operators[0].agent_ids == ()
