"""Tests for the world_cup trial builder registration and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# Force registration via the same import path the CLI uses.
import dojozero.world_cup  # noqa: F401
from dojozero.core._registry import get_trial_builder_definition
from dojozero.data._config import HubConfig
from dojozero.data._factory import get_store_factory
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

    def test_minimal_valid_params(self) -> None:
        params = WorldCupTrialParams(
            espn_game_id="760415",
            hub=self._hub(),
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
        )
        assert params.league == league

    def test_unknown_league_code_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            WorldCupTrialParams(
                espn_game_id="x",
                league="nfl",
                hub=self._hub(),
            )
        # Pydantic wraps our ValueError; the message text must surface.
        assert "Unknown FIFA league code" in str(exc_info.value)

    def test_example_params_validate(self) -> None:
        defn = get_trial_builder_definition("world_cup")
        assert defn is not None
        # `example_params` is just a dict; constructing the params class from
        # it must not raise (apart from KeyError-style mismatches).
        ep = defn.example_params or {}
        # `data_streams` / `operators` / `agents` shapes are validated by the
        # generic config models — only the params model is constructed here.
        WorldCupTrialParams.model_validate(
            {k: ep[k] for k in ("espn_game_id", "league", "hub")}
        )
