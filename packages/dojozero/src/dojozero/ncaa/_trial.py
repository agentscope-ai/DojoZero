"""Trial builder for NCAA betting scenario."""

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from dojozero.core import (
    RuntimeContext,
    DataStreamSpec,
    OperatorSpec,
    register_trial_builder,
    TrialSpec,
)
from dojozero.data._config import HubConfig, TrialDataStreamConfig
from dojozero.data._factory import build_runtime_context

# Import factories to ensure they are registered
import dojozero.data.ncaa._factory  # noqa: F401
import dojozero.data.websearch._factory  # noqa: F401
import dojozero.data.socialmedia  # noqa: F401
import dojozero.data.polymarket._factory  # noqa: F401
from dojozero.data.websearch._events import WebSearchEventMixin
from dojozero.data.socialmedia._events import SocialMediaEventMixin
from dojozero.ncaa._agent import (
    BettingAgent,
)
from dojozero.agents import (
    build_operator_to_agents_map,
    build_agent_specs,
    load_agent_configs_cached,
)
from dojozero.ncaa._datastream import (
    NCAAPreGameBettingDataHubDataStream,
    NCAAPreGameBettingDataHubDataStreamConfig,
)
from dojozero.data.ncaa._utils import get_game_info_by_id_async

# Import shared operators and metadata from betting module
from dojozero.betting import (
    BettingTrialMetadata,
    BrokerOperator,
    BrokerOperatorConfig,
    TrialBrokerConfig,
)
from dojozero.agents import SocialBoardActor

logger = logging.getLogger(__name__)


class NCAATrialParams(BaseModel):
    """Trial parameters for NCAA scenario."""

    # NCAA game configuration
    espn_game_id: str = Field(..., description="ESPN game ID (e.g., '401522202')")
    game_date: str | None = Field(
        default=None,
        description=(
            "Game date in YYYY-MM-DD format. If not provided, will try to:\n"
            "1. Fetch from ESPN API\n"
            "2. Extract from persistence_file path"
        ),
    )

    # Hub configuration (required)
    hub: HubConfig = Field(..., description="Hub configuration with persistence file")
    hub_id: str = Field(default="ncaa_hub")

    # Store configuration
    poll_interval_seconds: float = Field(default=30.0)

    # Data streams configuration (optional, hierarchical)
    data_streams: list[TrialDataStreamConfig] | None = Field(default=None)

    # Event type configuration
    event_types: list[str] = Field(
        default_factory=lambda: [
            "injury_report",
            "power_ranking",
            "expert_prediction",
            "twitter_top_tweets",
        ],
        description="List of canonical event type suffixes.",
    )

    # Operators configuration (optional, hierarchical)
    operators: list[TrialBrokerConfig] | None = Field(default=None)

    # Agent configuration
    agents: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of agent configurations.",
    )

    # Polymarket configuration
    market_url: str | None = Field(
        default=None,
        description="Optional Polymarket market URL.",
    )


async def _build_trial_spec(
    trial_id: str,
    params: NCAATrialParams,
) -> TrialSpec[BettingTrialMetadata]:
    """Return a TrialSpec that wires DataHub, streams, and agents together."""
    game_info = await get_game_info_by_id_async(params.espn_game_id)

    if not game_info:
        logger.error(
            "Could not find game info for espn_game_id=%s. Exiting.",
            params.espn_game_id,
        )
        raise ValueError(
            f"Could not find game info for espn_game_id={params.espn_game_id}."
        )

    # Extract typed fields from GameInfo
    home_tricode = game_info.home_team.tricode
    away_tricode = game_info.away_team.tricode
    home_team_name = game_info.home_team.name
    away_team_name = game_info.away_team.name
    home_team_id = game_info.home_team.team_id
    away_team_id = game_info.away_team.team_id
    season_year = game_info.season_year
    season_type = game_info.season_type
    game_date = params.game_date or game_info.get_game_date_us()

    logger.info(
        "Found game info: %s on %s",
        f"{away_tricode} @ {home_tricode}",
        game_date,
    )

    if not params.hub.persistence_file:
        raise ValueError(
            "hub.persistence_file is required. For auto-scheduled trials, ensure "
            "data_dir is set in the trial source config so the scheduler can "
            "populate this field."
        )
    persistence_file = params.hub.persistence_file

    if not game_date:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", persistence_file)
        if date_match:
            game_date = date_match.group(1)
            logger.info("Extracted game_date from persistence_file path: %s", game_date)

    hub_id = params.hub_id

    # Create stream specs
    stream_specs: list[DataStreamSpec[NCAAPreGameBettingDataHubDataStreamConfig]] = []

    def _build_stream_config(
        actor_id: str,
        event_type_suffixes: list[str],
    ) -> NCAAPreGameBettingDataHubDataStreamConfig:
        """Build a stream config dict from event type suffixes."""
        actual_event_types = [f"event.{suffix}" for suffix in event_type_suffixes]

        cfg: NCAAPreGameBettingDataHubDataStreamConfig = {
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

        # Check which event types need web search
        _ws_suffixes = {
            cls.model_fields["event_type"].default.removeprefix("event.")  # type: ignore[attr-defined]
            for cls in WebSearchEventMixin.__subclasses__()
        }
        websearch_suffixes = [
            suffix for suffix in event_type_suffixes if suffix in _ws_suffixes
        ]

        # Check which event types need ESPN stats fetch
        _stats_suffixes = {"pregame_stats"}
        stats_suffixes = [
            suffix for suffix in event_type_suffixes if suffix in _stats_suffixes
        ]

        # Check which event types need social media collection
        _sm_suffixes = {
            cls.model_fields["event_type"].default.removeprefix("event.")  # type: ignore[attr-defined]
            for cls in SocialMediaEventMixin.__subclasses__()
        }
        socialmedia_suffixes = [
            suffix for suffix in event_type_suffixes if suffix in _sm_suffixes
        ]

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

            logger.info(
                "Stream '%s' subscribes to: %s",
                ds_config.id,
                suffixes,
            )
            cfg = _build_stream_config(ds_config.id, suffixes)
            stream_specs.append(
                DataStreamSpec(
                    actor_id=ds_config.id,
                    actor_cls=NCAAPreGameBettingDataHubDataStream,
                    config=cfg,
                )
            )
    else:
        for suffix in params.event_types:
            cfg = _build_stream_config(f"{suffix}_stream", [suffix])
            stream_specs.append(
                DataStreamSpec(
                    actor_id=f"{suffix}_stream",
                    actor_cls=NCAAPreGameBettingDataHubDataStream,
                    config=cfg,
                )
            )

    # Validate referenced streams exist
    defined_stream_ids = {spec.actor_id for spec in stream_specs}
    referenced_stream_ids: set[str] = set()

    if params.operators:
        for op_config in params.operators:
            if op_config.data_streams:
                referenced_stream_ids.update(op_config.data_streams)

    if params.agents:
        for agent_dict in params.agents:
            agent_streams = agent_dict.get("data_streams", [])
            if agent_streams:
                referenced_stream_ids.update(agent_streams)

    missing_streams = referenced_stream_ids - defined_stream_ids
    if missing_streams:
        raise ValueError(
            f"The following streams are referenced but not defined: {sorted(missing_streams)}. "
            f"Please add them to the 'data_streams' section."
        )

    # Build operator -> agent mapping
    config_cache = load_agent_configs_cached(params.agents) if params.agents else {}
    operator_to_agents = (
        build_operator_to_agents_map(params.agents, config_cache)
        if params.agents
        else {}
    )

    # Create operators
    if not params.operators:
        raise ValueError(
            "No operators specified. At least one operator with class 'BrokerOperator' is required."
        )

    operator_specs = []
    operator_class_map = {
        "BrokerOperator": BrokerOperator,
    }
    for op_config in params.operators:
        op_cls = operator_class_map.get(op_config.class_name)
        if op_cls is None:
            raise ValueError(f"Unknown operator class: {op_config.class_name}")

        if op_config.class_name == "BrokerOperator":
            broker_config: BrokerOperatorConfig = {
                "actor_id": op_config.id,
            }
            if op_config.initial_balance:
                broker_config["initial_balance"] = op_config.initial_balance
            if op_config.allowed_tools:
                broker_config["allowed_tools"] = op_config.allowed_tools
            operator_config: BrokerOperatorConfig = broker_config
        else:
            raise ValueError(f"Unsupported operator class: {op_config.class_name}")

        data_stream_ids = op_config.data_streams if op_config.data_streams else []

        operator_spec = OperatorSpec(
            actor_id=op_config.id,
            actor_cls=op_cls,
            config=operator_config,
            data_stream_ids=tuple(data_stream_ids),
            agent_ids=tuple(operator_to_agents.get(op_config.id, [])),
        )
        operator_specs.append(operator_spec)
        logger.info(
            "Created operator '%s' of class '%s' with stream subscriptions: %s, agent_ids: %s",
            op_config.id,
            op_config.class_name,
            data_stream_ids,
            operator_spec.agent_ids,
        )

    # Create agent specs
    if not params.agents:
        raise ValueError(
            "No agents specified. At least one agent with class 'BettingAgent' is required."
        )

    agent_specs = build_agent_specs(
        agents=params.agents,
        agent_cls=BettingAgent,
        allowed_class_names={"BettingAgent"},
        config_cache=config_cache,
    )
    if not agent_specs:
        logger.warning(
            "No agents enabled for this trial: no LLM API keys matched your "
            "agents/llms config. Continuing with data streams and broker only."
        )

    # Create SocialBoard if multiple agents
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
        logger.info(
            "Created SocialBoard for trial '%s' with %d agents",
            trial_id,
            len(agent_specs),
        )

    # Build typed metadata
    metadata = BettingTrialMetadata(
        hub_id=hub_id,
        persistence_file=persistence_file,
        store_types=("ncaa", "polymarket"),
        sample="ncaa",
        sport_type="ncaa",
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
    )

    return TrialSpec(
        trial_id=trial_id,
        metadata=metadata,
        data_streams=tuple(stream_specs),
        operators=tuple(operator_specs),
        agents=tuple(agent_specs),
        social_board=social_board,
    )


def _build_ncaa_runtime_context(
    spec: TrialSpec[BettingTrialMetadata],
) -> RuntimeContext:
    """Build runtime context for NCAA betting trial."""
    metadata = spec.metadata

    hub_id = metadata.hub_id
    persistence_file = metadata.persistence_file
    store_types = list(metadata.store_types)

    return build_runtime_context(
        trial_id=spec.trial_id,
        hub_id=hub_id,
        persistence_file=persistence_file,
        metadata=metadata,
        store_types=store_types,
        sport_type=metadata.sport_type,
    )


register_trial_builder(
    "ncaa",
    NCAATrialParams,
    _build_trial_spec,
    description="NCAA basketball betting scenario with relevant data inputs",
    context_builder=_build_ncaa_runtime_context,
    example_params={
        "espn_game_id": "401522202",
        "hub": {
            "persistence_file": "outputs/ncaa_events.jsonl",
        },
        "data_streams": [
            {
                "id": "pre_game_insights_stream",
                "event_types": [
                    "injury_report",
                    "power_ranking",
                    "expert_prediction",
                    "pregame_stats",
                    "twitter_top_tweets",
                ],
            },
            {
                "id": "game_lifecycle_stream",
                "event_types": ["game_initialize", "game_start", "game_result"],
            },
            {
                "id": "game_update_stream",
                "event_types": ["ncaa_game_update"],
            },
            {
                "id": "odds_update_stream",
                "event_types": ["odds_update"],
            },
            {
                "id": "play_by_play_stream",
                "event_types": ["ncaa_play"],
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
                    "place_bet_moneyline",
                    "cancel_bet",
                    "get_active_bets",
                    "get_pending_orders",
                    "get_bet_history",
                    "get_statistics",
                ],
                "data_streams": [
                    "game_lifecycle_stream",
                    "odds_update_stream",
                    "game_update_stream",
                ],
            },
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
