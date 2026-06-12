"""Trial builder for World Cup (FIFA soccer) betting scenario."""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from dojozero.core import (
    DataStreamSpec,
    OperatorSpec,
    RuntimeContext,
    TrialSpec,
    register_trial_builder,
)
from dojozero.data._config import HubConfig, TrialDataStreamConfig
from dojozero.data._factory import build_runtime_context

# Import factories to ensure they are registered when this module loads.
import dojozero.data.world_cup._factory  # noqa: F401
import dojozero.data.websearch._factory  # noqa: F401
import dojozero.data.socialmedia  # noqa: F401
import dojozero.data.polymarket._factory  # noqa: F401

from dojozero.agents import (
    SocialBoardActor,
    build_agent_specs,
    build_operator_to_agents_map,
    load_agent_configs_cached,
)
from dojozero.betting import (
    BettingTrialMetadata,
    BrokerOperator,
    BrokerOperatorConfig,
    PredictionBroker,
    PredictionBrokerConfig,
    TrialBrokerConfig,
)
from dojozero.data.socialmedia._events import SocialMediaEventMixin
from dojozero.data.websearch._events import WebSearchEventMixin
from dojozero.data.world_cup._api import DEFAULT_LEAGUE
from dojozero.data.world_cup._constants import WORLD_CUP_KNOWN_LEAGUES
from dojozero.data.world_cup._utils import (
    get_game_info_by_id_async,
    validate_world_cup_league,
)
from dojozero.world_cup._agent import BettingAgent
from dojozero.world_cup._datastream import (
    WorldCupPreGameBettingDataHubDataStream,
    WorldCupPreGameBettingDataHubDataStreamConfig,
)

logger = logging.getLogger(__name__)


class WorldCupTrialParams(BaseModel):
    """Trial parameters for the World Cup scenario."""

    espn_game_id: str = Field(..., description="ESPN event ID for the match")
    game_date: str | None = Field(
        default=None,
        description="Match date in YYYY-MM-DD (auto-derived from ESPN if absent)",
    )
    league: str = Field(
        default=DEFAULT_LEAGUE,
        description=(
            "FIFA league code. Accepted values: "
            + ", ".join(sorted(WORLD_CUP_KNOWN_LEAGUES))
        ),
    )

    hub: HubConfig = Field(..., description="Hub configuration with persistence file")
    hub_id: str = Field(default="world_cup_hub")

    poll_interval_seconds: float = Field(default=30.0)

    data_streams: list[TrialDataStreamConfig] | None = Field(default=None)

    event_types: list[str] = Field(
        default_factory=lambda: [
            "injury_report",
            "power_ranking",
            "expert_prediction",
            "twitter_top_tweets",
        ],
        description=(
            "Canonical event type suffixes used when ``data_streams`` is not "
            "provided. The 'event.' prefix is added automatically."
        ),
    )

    operators: list[TrialBrokerConfig] | None = Field(default=None)

    agents: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Agent configurations (mirrors NBA trial shape)",
    )

    market_url: str | None = Field(
        default=None,
        description=(
            "Optional Polymarket game-market URL. If omitted, the Polymarket "
            "store attempts to construct a world-cup-{away}-{home}-{date} slug."
        ),
    )

    @field_validator("league", mode="after")
    @classmethod
    def _validate_league(cls, value: str) -> str:
        return validate_world_cup_league(value)

    @model_validator(mode="after")
    def _validate_operators(self) -> "WorldCupTrialParams":
        if not self.operators:
            raise ValueError(
                "No operators specified. At least one BrokerOperator is required."
            )
        return self


async def _build_trial_spec(
    trial_id: str,
    params: WorldCupTrialParams,
) -> TrialSpec[BettingTrialMetadata]:
    """Build the trial spec wiring DataHub, streams, operators, and agents."""
    game_info = await get_game_info_by_id_async(
        params.espn_game_id, league=params.league
    )
    if not game_info:
        raise ValueError(
            f"Could not find game info for espn_game_id={params.espn_game_id} "
            f"under league={params.league}."
        )

    home_tricode = game_info.home_team.tricode
    away_tricode = game_info.away_team.tricode
    home_team_name = game_info.home_team.name
    away_team_name = game_info.away_team.name
    home_team_id = game_info.home_team.team_id
    away_team_id = game_info.away_team.team_id
    season_year = game_info.season_year
    season_type = game_info.season_type
    game_date = params.game_date or game_info.get_game_date_us()
    operators = params.operators or []

    logger.info(
        "World Cup trial '%s': %s vs %s on %s (league=%s)",
        trial_id,
        away_tricode or away_team_name,
        home_tricode or home_team_name,
        game_date,
        params.league,
    )

    if not params.hub.persistence_file:
        raise ValueError(
            "hub.persistence_file is required. Ensure output_dir is set in the "
            "trial source config when auto-scheduling."
        )
    persistence_file = params.hub.persistence_file.replace(
        "{espn_game_id}", params.espn_game_id
    )
    if not game_date:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", persistence_file)
        if date_match:
            game_date = date_match.group(1)

    hub_id = params.hub_id
    stream_specs: list[
        DataStreamSpec[WorldCupPreGameBettingDataHubDataStreamConfig]
    ] = []

    def _build_stream_config(
        actor_id: str,
        event_type_suffixes: list[str],
    ) -> WorldCupPreGameBettingDataHubDataStreamConfig:
        actual_event_types = [f"event.{suffix}" for suffix in event_type_suffixes]
        cfg: WorldCupPreGameBettingDataHubDataStreamConfig = {
            "actor_id": actor_id,
            "hub_id": hub_id,
            "persistence_file": persistence_file,
            "event_type": actual_event_types[0] if actual_event_types else "",
            "event_types": actual_event_types,
        }
        if home_tricode:
            cfg["home_team_tricode"] = home_tricode
        if away_tricode:
            cfg["away_team_tricode"] = away_tricode

        def _event_type_suffix(cls: type[Any]) -> str:
            field = getattr(cls, "model_fields", {}).get("event_type")
            default = getattr(field, "default", "")
            return default.removeprefix("event.") if isinstance(default, str) else ""

        _ws_suffixes = {
            suffix
            for cls in WebSearchEventMixin.__subclasses__()
            if (suffix := _event_type_suffix(cls))
        }
        websearch_suffixes = [s for s in event_type_suffixes if s in _ws_suffixes]

        _stats_suffixes = {"pregame_stats"}
        stats_suffixes = [s for s in event_type_suffixes if s in _stats_suffixes]

        _sm_suffixes = {
            suffix
            for cls in SocialMediaEventMixin.__subclasses__()
            if (suffix := _event_type_suffix(cls))
        }
        socialmedia_suffixes = [s for s in event_type_suffixes if s in _sm_suffixes]

        if websearch_suffixes or stats_suffixes or socialmedia_suffixes:
            cfg["game_id"] = params.espn_game_id
            if home_team_name:
                cfg["home_team_name"] = home_team_name
            if away_team_name:
                cfg["away_team_name"] = away_team_name
            if game_date:
                cfg["game_date"] = game_date

        if websearch_suffixes:
            cfg["websearch_event_types"] = websearch_suffixes

        if stats_suffixes:
            cfg["stats_event_types"] = stats_suffixes
            cfg["home_team_id"] = home_team_id
            cfg["away_team_id"] = away_team_id
            cfg["season_year"] = season_year
            cfg["season_type"] = season_type

        if socialmedia_suffixes:
            cfg["socialmedia_event_types"] = socialmedia_suffixes

        cfg["league"] = params.league
        return cfg

    if params.data_streams:
        for ds_config in params.data_streams:
            suffixes: list[str] = []
            if ds_config.event_types:
                suffixes = list(ds_config.event_types)
            elif ds_config.event_type:
                suffixes = [ds_config.event_type]
            if not suffixes:
                logger.warning("Stream '%s' has no event types, skipping", ds_config.id)
                continue
            cfg = _build_stream_config(ds_config.id, suffixes)
            stream_specs.append(
                DataStreamSpec(
                    actor_id=ds_config.id,
                    actor_cls=WorldCupPreGameBettingDataHubDataStream,
                    config=cfg,
                )
            )
    else:
        for suffix in params.event_types:
            cfg = _build_stream_config(f"{suffix}_stream", [suffix])
            stream_specs.append(
                DataStreamSpec(
                    actor_id=f"{suffix}_stream",
                    actor_cls=WorldCupPreGameBettingDataHubDataStream,
                    config=cfg,
                )
            )

    # Validate all referenced streams exist
    defined_stream_ids = {spec.actor_id for spec in stream_specs}
    referenced_stream_ids: set[str] = set()
    for op_config in operators:
        if op_config.data_streams:
            referenced_stream_ids.update(op_config.data_streams)
    if params.agents:
        for agent_dict in params.agents:
            referenced_stream_ids.update(agent_dict.get("data_streams", []) or [])
    missing_streams = referenced_stream_ids - defined_stream_ids
    if missing_streams:
        raise ValueError(
            "The following streams are referenced by operators/agents but are "
            f"not defined in YAML: {sorted(missing_streams)}."
        )

    config_cache = load_agent_configs_cached(params.agents) if params.agents else {}
    operator_to_agents = (
        build_operator_to_agents_map(params.agents, config_cache)
        if params.agents
        else {}
    )

    operator_specs: list[OperatorSpec[Any]] = []
    operator_class_map = {
        "BrokerOperator": BrokerOperator,
        "PredictionBroker": PredictionBroker,
    }
    for op_config in operators:
        op_cls = operator_class_map.get(op_config.class_name)
        if op_cls is None:
            raise ValueError(f"Unknown operator class: {op_config.class_name}")

        operator_config: BrokerOperatorConfig | PredictionBrokerConfig
        if op_config.class_name == "BrokerOperator":
            broker_config: BrokerOperatorConfig = {"actor_id": op_config.id}
            if op_config.initial_balance:
                broker_config["initial_balance"] = op_config.initial_balance
            if op_config.allowed_tools:
                broker_config["allowed_tools"] = op_config.allowed_tools
            operator_config = broker_config
        elif op_config.class_name == "PredictionBroker":
            prediction_config: PredictionBrokerConfig = {"actor_id": op_config.id}
            if op_config.allowed_tools:
                prediction_config["allowed_tools"] = op_config.allowed_tools
            if op_config.window_pools is not None:
                prediction_config["window_pools"] = op_config.window_pools
            operator_config = prediction_config
        else:
            raise ValueError(f"Unsupported operator class: {op_config.class_name}")

        data_stream_ids = op_config.data_streams if op_config.data_streams else []
        operator_specs.append(
            OperatorSpec(
                actor_id=op_config.id,
                actor_cls=op_cls,
                config=operator_config,
                data_stream_ids=tuple(data_stream_ids),
                agent_ids=tuple(operator_to_agents.get(op_config.id, [])),
            )
        )

    agent_specs = (
        build_agent_specs(
            agents=params.agents,
            agent_cls=BettingAgent,
            allowed_class_names={"BettingAgent"},
            config_cache=config_cache,
        )
        if params.agents
        else []
    )

    social_board: OperatorSpec[Any] | None = None
    if len(agent_specs) > 1:
        social_board_actor_id = "social_board"
        social_board = OperatorSpec(
            actor_id=social_board_actor_id,
            actor_cls=SocialBoardActor,
            config={
                "trial_id": trial_id,
                "actor_id": social_board_actor_id,
            },
        )

    metadata = BettingTrialMetadata(
        hub_id=hub_id,
        persistence_file=persistence_file,
        store_types=("world_cup", "polymarket"),
        sample="world_cup",
        sport_type="world_cup",
        espn_game_id=params.espn_game_id,
        event_types=tuple(params.event_types),
        home_tricode=home_tricode,
        away_tricode=away_tricode,
        home_team_name=home_team_name,
        away_team_name=away_team_name,
        game_date=game_date,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        market_url=params.market_url,
        world_cup_league=params.league,
    )

    return TrialSpec(
        trial_id=trial_id,
        metadata=metadata,
        data_streams=tuple(stream_specs),
        operators=tuple(operator_specs),
        agents=tuple(agent_specs),
        social_board=social_board,
    )


def _build_world_cup_runtime_context(
    spec: TrialSpec[BettingTrialMetadata],
) -> RuntimeContext:
    """Build runtime context for a World Cup trial."""
    metadata = spec.metadata
    return build_runtime_context(
        trial_id=spec.trial_id,
        hub_id=metadata.hub_id,
        persistence_file=metadata.persistence_file,
        metadata=metadata,
        store_types=list(metadata.store_types),
        sport_type=metadata.sport_type,
    )


register_trial_builder(
    "world_cup",
    WorldCupTrialParams,
    _build_trial_spec,
    description="World Cup (FIFA soccer) betting scenario",
    context_builder=_build_world_cup_runtime_context,
    example_params={
        "espn_game_id": "760415",
        "league": "fifa.world",
        "hub": {
            "persistence_file": "outputs/world_cup_events-{espn_game_id}.jsonl",
        },
        "data_streams": [
            {
                "id": "pre_game_insights_stream",
                "event_types": [
                    "injury_report",
                    "power_ranking",
                    "expert_prediction",
                    "twitter_top_tweets",
                ],
            },
            {
                "id": "game_lifecycle_stream",
                "event_types": ["game_initialize", "game_start", "game_result"],
            },
            {
                "id": "game_update_stream",
                "event_types": ["world_cup_game_update"],
            },
            {
                "id": "odds_update_stream",
                "event_types": ["odds_update"],
            },
            {
                "id": "play_by_play_stream",
                "event_types": ["world_cup_play"],
            },
        ],
        "operators": [
            {
                "id": "betting_broker",
                "class": "BrokerOperator",
                "initial_balance": "1000.00",
                "allowed_tools": [
                    "get_balance",
                    "get_event",
                    "place_market_bet_moneyline",
                    "get_holdings",
                    "get_bet_history",
                    "get_statistics",
                ],
                "data_streams": [
                    "game_lifecycle_stream",
                    "odds_update_stream",
                    "game_update_stream",
                ],
            }
        ],
        "agents": [
            {
                "id": "betting_agent",
                "class": "BettingAgent",
                "operators": ["betting_broker"],
                "data_streams": [
                    "pre_game_insights_stream",
                    "game_update_stream",
                    "odds_update_stream",
                    "game_lifecycle_stream",
                    "play_by_play_stream",
                ],
            }
        ],
    },
)


__all__ = ["WorldCupTrialParams"]
