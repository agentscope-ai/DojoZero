"""Trial builder for NBA betting scenario."""

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
from dojozero.data._config import DataStreamConfig, HubConfig
from dojozero.data._factory import build_runtime_context

# Import factories to ensure they are registered
import dojozero.data.nba._factory  # noqa: F401
import dojozero.data.websearch._factory  # noqa: F401
import dojozero.data.polymarket._factory  # noqa: F401
from dojozero.data.websearch._processors import (
    ExpertPredictionProcessor,
    InjurySummaryProcessor,
    PowerRankingProcessor,
)
from dojozero.nba._agent import (
    BettingAgent,
)
from dojozero.agents import (
    build_operator_to_agents_map,
    build_agent_specs,
    load_agent_configs_cached,
)
from dojozero.nba._datastream import (
    NBAPreGameBettingDataHubDataStream,
    NBAPreGameBettingDataHubDataStreamConfig,
)
from dojozero.data._models import EventTypes
from dojozero.data.nba._utils import get_game_info_by_id_async

# Import shared operators and metadata from betting module
from dojozero.betting import (
    BettingTrialMetadata,
    BrokerOperator,
    BrokerOperatorConfig,
    TrialBrokerConfig,
)

logger = logging.getLogger(__name__)

# Mapping from event_type to (processor_class, source_event_types)
# This defines which processors are needed for each event type and what they depend on.
# Used to auto-register processors on stores when event types are requested.
# Note: source_event_types must use full event type values (e.g., "event.raw_web_search")
# to match the event_type property of DataEvent subclasses
EVENT_TYPE_PROCESSOR_MAP: dict[str, tuple[type[Any] | None, list[str]]] = {
    # Raw stream: no processor, emitted directly from store
    "raw_web_search": (None, []),
    # Processed streams: processor class and source event types
    "injury_summary": (InjurySummaryProcessor, ["event.raw_web_search"]),
    "power_ranking": (PowerRankingProcessor, ["event.raw_web_search"]),
    "expert_prediction": (ExpertPredictionProcessor, ["event.raw_web_search"]),
}

# Mapping from synthetic event types to actual event types
# Used when a stream subscribes to multiple event types
SYNTHETIC_EVENT_TYPE_MAP: dict[str, list[str]] = {
    "game_status_change": [
        EventTypes.GAME_START.value,
        EventTypes.GAME_RESULT.value,
        EventTypes.GAME_INITIALIZE.value,
    ],
    # Other event types (game_update, odds_update) are direct mappings
}


class NBATrialParams(BaseModel):
    """Trial parameters for NBA scenario."""

    # NBA game configuration
    espn_game_id: str = Field(..., description="ESPN game ID (e.g., '401810490')")
    game_date: str | None = Field(
        default=None,
        description=(
            "Game date in YYYY-MM-DD format. If not provided, will try to:\n"
            "1. Fetch from ESPN API\n"
            "2. Extract from persistence_file path (e.g., 'data/nba-betting/2026-01-15/...')"
        ),
    )

    # Hub configuration (required)
    hub: HubConfig = Field(..., description="Hub configuration with persistence file")
    hub_id: str = Field(default="nba_hub")

    # Store configuration
    websearch_store_id: str = Field(default="websearch_store")
    poll_interval_seconds: float = Field(default=30.0)

    # Data streams configuration (optional, hierarchical)
    data_streams: list[DataStreamConfig] | None = Field(default=None)

    # Event type configuration (which event types to create streams for) - used if data_streams not provided
    event_types: list[str] = Field(
        default_factory=lambda: [
            "raw_web_search",
            "injury_summary",
            "power_ranking",
            "expert_prediction",
        ],
        description="List of event types to create streams for (used if data_streams not provided)",
    )

    # Operators configuration (optional, hierarchical)
    operators: list[TrialBrokerConfig] | None = Field(default=None)

    # Agent configuration
    agents: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "List of agent configurations. Each agent dict should have:\n"
            "  - 'id': str (required) - Agent identifier\n"
            "  - 'class': str (required) - Must be 'BettingAgent'\n"
            "  - 'operators': list[str] (optional) - Operator IDs to register\n"
            "  - 'data_streams': list[str] (optional) - DataStream actor IDs to subscribe to\n"
            "  - 'persona_config_path': str (optional) - Path to persona YAML config file\n"
            "  - 'llm_config_path': str (optional) - Path to LLM YAML config file"
        ),
    )

    # Polymarket configuration
    market_url: str | None = Field(
        default=None,
        description=(
            "Optional Polymarket market URL (e.g., 'https://polymarket.com/sports/nba/games/week/3/nba-sas-lal-2025-12-10'). "
            "If not provided, will auto-construct slug from game info (away_tricode, home_tricode, game_date)."
        ),
    )

    # Search queries (optional, for triggering searches)
    # If not provided, will be auto-generated based on espn_game_id
    # Supports query templates with placeholders: {teams}, {home_team}, {away_team}, {date}, {home_tricode}, {away_tricode}
    # Use "template" field for templates or "query" field for literal queries
    search_queries: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Custom search queries. Each dict can have:\n"
            "  - 'template': str (optional) - Query template with placeholders\n"
            "  - 'query': str (optional) - Literal query string (if no template)\n"
            "  - 'intent': str (optional) - One of 'injury_summary', 'power_ranking', 'expert_prediction'\n"
            "Available template placeholders:\n"
            "  - {teams} - 'Away Team vs Home Team'\n"
            "  - {home_team} - Home team full name\n"
            "  - {away_team} - Away team full name\n"
            "  - {date} - Game date\n"
            "  - {home_tricode} - Home team tricode (e.g., 'LAL')\n"
            "  - {away_tricode} - Away team tricode (e.g., 'SAS')"
        ),
    )


async def _build_trial_spec(
    trial_id: str,
    params: NBATrialParams,
) -> TrialSpec[BettingTrialMetadata]:
    """Return a :class:`TrialSpec` that wires DataHub, streams, and agents together."""
    # Get game information from espn_game_id to extract team tricodes and names
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
    # Use provided game_date if available, otherwise use from game_info
    game_date = params.game_date or game_info.get_game_date_us()

    logger.info(
        "Found game info: %s on %s",
        f"{away_tricode} @ {home_tricode}",
        game_date,
    )

    # Validate that persistence_file is set (required for building trial)
    if not params.hub.persistence_file:
        raise ValueError(
            "hub.persistence_file is required. For auto-scheduled trials, ensure "
            "data_dir is set in the trial source config so the scheduler can "
            "populate this field."
        )
    persistence_file = params.hub.persistence_file

    # Fallback: extract game_date from persistence_file path if still not available
    # Path format: data/nba-betting/YYYY-MM-DD/espn_game_id.jsonl
    if not game_date:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", persistence_file)
        if date_match:
            game_date = date_match.group(1)
            logger.info("Extracted game_date from persistence_file path: %s", game_date)

    # Extract hub configuration
    hub_id = params.hub_id

    # Extract event_types from data_streams if provided, otherwise use event_types field
    if params.data_streams:
        event_types_list = [ds.event_type for ds in params.data_streams]
        logger.info(
            "Extracted event types from data_streams config: %s",
            event_types_list,
        )
    else:
        event_types_list = params.event_types
        logger.info(
            "Using event_types from params: %s",
            event_types_list,
        )

    # Create stream specs - multiple streams, one per event type (or group)
    # All streams subscribe to the same DataHub
    stream_specs = []

    # Create streams for web search event types
    if params.data_streams:
        # Use hierarchical data_streams config
        for ds_config in params.data_streams:
            # Determine actual event types for this stream
            # Check if it's a synthetic type that maps to multiple event types
            if ds_config.event_type in SYNTHETIC_EVENT_TYPE_MAP:
                actual_event_types = SYNTHETIC_EVENT_TYPE_MAP[ds_config.event_type]
            else:
                actual_event_types = [ds_config.event_type]

            ds_stream_config: NBAPreGameBettingDataHubDataStreamConfig = {
                "actor_id": ds_config.id,
                "hub_id": hub_id,
                "persistence_file": persistence_file,
                "event_type": ds_config.event_type,
                "event_types": actual_event_types,
            }

            # Add optional fields
            if home_tricode:
                ds_stream_config["home_team_tricode"] = home_tricode
            if away_tricode:
                ds_stream_config["away_team_tricode"] = away_tricode

            # Handle initializer config for raw_web_search stream
            if ds_config.event_type == "raw_web_search":
                ds_stream_config["websearch_store_id"] = params.websearch_store_id
                if home_tricode:
                    ds_stream_config["home_team_tricode"] = home_tricode
                if away_tricode:
                    ds_stream_config["away_team_tricode"] = away_tricode
                if home_team_name:
                    ds_stream_config["home_team_name"] = home_team_name
                if away_team_name:
                    ds_stream_config["away_team_name"] = away_team_name
                if game_date:
                    ds_stream_config["game_date"] = game_date
                # Get search_queries from initializer if provided
                if ds_config.initializer and "search_queries" in ds_config.initializer:
                    ds_stream_config["search_queries"] = ds_config.initializer[
                        "search_queries"
                    ]

            stream_spec = DataStreamSpec(
                actor_id=ds_config.id,
                actor_cls=NBAPreGameBettingDataHubDataStream,
                config=ds_stream_config,
            )
            stream_specs.append(stream_spec)
    else:
        # Fallback to flat event_types structure - create one stream per event type
        for event_type in event_types_list:
            flat_stream_config: NBAPreGameBettingDataHubDataStreamConfig = {
                "actor_id": f"{event_type}_stream",
                "hub_id": hub_id,
                "persistence_file": persistence_file,
                "event_type": event_type,
                "event_types": [event_type],
            }

            # Add optional fields
            if home_tricode:
                flat_stream_config["home_team_tricode"] = home_tricode
            if away_tricode:
                flat_stream_config["away_team_tricode"] = away_tricode

            # Handle initializer config for raw_web_search stream
            if event_type == "raw_web_search":
                flat_stream_config["websearch_store_id"] = params.websearch_store_id
                if home_tricode:
                    flat_stream_config["home_team_tricode"] = home_tricode
                if away_tricode:
                    flat_stream_config["away_team_tricode"] = away_tricode
                if home_team_name:
                    flat_stream_config["home_team_name"] = home_team_name
                if away_team_name:
                    flat_stream_config["away_team_name"] = away_team_name
                if game_date:
                    flat_stream_config["game_date"] = game_date
                # Pass search_queries if provided
                if params.search_queries:
                    flat_stream_config["search_queries"] = params.search_queries

            stream_spec = DataStreamSpec(
                actor_id=f"{event_type}_stream",
                actor_cls=NBAPreGameBettingDataHubDataStream,
                config=flat_stream_config,
            )
            stream_specs.append(stream_spec)

    # Validate that all referenced streams exist
    # Collect all stream IDs that are defined in YAML
    defined_stream_ids = set()
    if params.data_streams:
        defined_stream_ids = {ds.id for ds in params.data_streams}

    # Collect all stream IDs referenced by operators and agents
    referenced_stream_ids = set()

    # Check operators
    if params.operators:
        for op_config in params.operators:
            if op_config.data_streams:
                referenced_stream_ids.update(op_config.data_streams)

    # Check agents
    if params.agents:
        for agent_dict in params.agents:
            agent_streams = agent_dict.get("data_streams", [])
            if agent_streams:
                referenced_stream_ids.update(agent_streams)

    # Validate all referenced streams exist
    missing_streams = referenced_stream_ids - defined_stream_ids
    if missing_streams:
        raise ValueError(
            f"The following streams are referenced by operators/agents but are not defined in YAML: {sorted(missing_streams)}. "
            f"Please add them to the 'data_streams' section in your configuration."
        )

    # Build operator_id -> agent_ids mapping from agent configs
    # For agents with config paths, expand to include all model-specific agent IDs
    # Load configs once and reuse to avoid redundant disk I/O
    config_cache = load_agent_configs_cached(params.agents) if params.agents else {}
    operator_to_agents = (
        build_operator_to_agents_map(params.agents, config_cache)
        if params.agents
        else {}
    )

    # Create operators - require explicit operator configuration
    if not params.operators:
        raise ValueError(
            "No operators specified. At least one operator with class 'BrokerOperator' is required."
        )

    operator_specs = []
    # Use hierarchical operators config
    operator_class_map = {
        "BrokerOperator": BrokerOperator,
    }
    for op_config in params.operators:
        op_cls = operator_class_map.get(op_config.class_name)
        if op_cls is None:
            raise ValueError(f"Unknown operator class: {op_config.class_name}")

        # Create operator config based on class
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

        # Use operator's specified data_streams, or default to empty
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

    # Create agent specs from agents config
    # Agents with config paths are expanded into multiple agents (one per model)
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

    # Build typed metadata with game information and hub config
    # This metadata is used by build_runtime_context and store factories
    metadata = BettingTrialMetadata(
        # Base fields
        hub_id=hub_id,
        persistence_file=persistence_file,
        store_types=("nba", "websearch", "polymarket"),
        # Betting fields
        sample="nba",
        sport_type="nba",
        espn_game_id=params.espn_game_id,
        event_types=tuple(params.event_types),
        # Team info
        home_tricode=home_tricode,
        away_tricode=away_tricode,
        home_team_name=home_team_name,
        away_team_name=away_team_name,
        game_date=game_date,
        # Market URL (optional)
        market_url=params.market_url,
    )

    return TrialSpec(
        trial_id=trial_id,
        metadata=metadata,
        data_streams=tuple(stream_specs),
        operators=tuple(operator_specs),
        agents=tuple(agent_specs),
    )


def _build_nba_runtime_context(
    spec: TrialSpec[BettingTrialMetadata],
) -> RuntimeContext:
    """Build runtime context for NBA betting trial.

    Uses the generic build_runtime_context with registered store factories
    to create DataHub and store instances.

    Args:
        spec: Trial specification with typed BettingTrialMetadata

    Returns:
        RuntimeContext with trial_id, data_hubs, stores, and startup callback
    """
    metadata = spec.metadata  # Already typed!

    # Direct attribute access - type-safe
    hub_id = metadata.hub_id
    persistence_file = metadata.persistence_file
    store_types = list(metadata.store_types)

    # Build and return RuntimeContext directly
    return build_runtime_context(
        trial_id=spec.trial_id,
        hub_id=hub_id,
        persistence_file=persistence_file,
        metadata=metadata,
        store_types=store_types,
        sport_type=metadata.sport_type,
    )


register_trial_builder(
    "nba",
    NBATrialParams,
    _build_trial_spec,
    description="NBA betting scenario with relevant data inputs",
    context_builder=_build_nba_runtime_context,
    example_params={
        "espn_game_id": "401810490",
        "hub": {
            "persistence_file": "outputs/nba_events.jsonl",
        },
        "data_streams": [
            {
                "id": "raw_web_search_stream",
                "event_type": "event.raw_web_search",
                "initializer": {
                    "search_queries": [
                        {
                            "template": "NBA injury updates for {teams} on {date}",
                            "intent": "injury_summary",
                        },
                        {"template": "NBA power rankings", "intent": "power_ranking"},
                        {
                            "template": "NBA expert predictions for {teams}",
                            "intent": "expert_prediction",
                        },
                    ]
                },
            },
            {
                "id": "injury_summary_stream",
                "event_type": "event.injury_summary",
            },
            {
                "id": "power_ranking_stream",
                "event_type": "event.power_ranking",
            },
            {
                "id": "expert_prediction_stream",
                "event_type": "event.expert_prediction",
            },
            {
                "id": "game_status_change_stream",
                "event_type": "event.game_status_change",
            },
            {
                "id": "game_update_stream",
                "event_type": "event.game_update",
            },
            {
                "id": "odds_update_stream",
                "event_type": "event.odds_update",
            },
            {
                "id": "play_by_play_stream",
                "event_type": "event.play_by_play",
            },
        ],
        "operators": [
            {
                "id": "betting_broker",
                "class": "BrokerOperator",
                "initial_balance": "1000.00",
                "data_streams": [
                    "game_status_change_stream",
                    "odds_update_stream",
                ],
            },
        ],
        "agents": [
            {
                "id": "betting_agent",
                "class": "BettingAgent",
                "operators": ["betting_broker"],
                "data_streams": [
                    "injury_summary_stream",
                    "power_ranking_stream",
                    "expert_prediction_stream",
                    "game_update_stream",
                    "odds_update_stream",
                    "game_status_change_stream",
                    "play_by_play_stream",
                ],
            }
        ],
    },
)
