"""Trial builder for NFL betting scenario."""

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
import dojozero.data.nfl._factory  # noqa: F401
import dojozero.data.websearch._factory  # noqa: F401
from dojozero.data.websearch._processors import (
    ExpertPredictionProcessor,
    InjurySummaryProcessor,
    PowerRankingProcessor,
)
from dojozero.nfl._agent import (
    BettingAgent,
)
from dojozero.agents import (
    build_operator_to_agents_map,
    build_agent_specs,
    load_agent_configs_cached,
)
from dojozero.nfl._datastream import (
    NFLPreGameBettingDataHubDataStream,
    NFLPreGameBettingDataHubDataStreamConfig,
)

# Import shared operators from betting module
from dojozero.betting import (
    BrokerOperator,
    BrokerOperatorConfig,
    TrialBrokerConfig,
)

logger = logging.getLogger(__name__)

# Mapping from event_type to (processor_class, source_event_types)
# This defines which processors are needed for each event type and what they depend on.
# Used to auto-register processors on stores when event types are requested.
EVENT_TYPE_PROCESSOR_MAP: dict[str, tuple[type[Any] | None, list[str]]] = {
    # Raw stream: no processor, emitted directly from store
    "raw_web_search": (None, []),
    # Processed streams: processor class and source event types
    "injury_summary": (InjurySummaryProcessor, ["raw_web_search"]),
    "power_ranking": (PowerRankingProcessor, ["raw_web_search"]),
    "expert_prediction": (ExpertPredictionProcessor, ["raw_web_search"]),
}

# Mapping from synthetic event types to actual event types
# Used when a stream subscribes to multiple event types
SYNTHETIC_EVENT_TYPE_MAP: dict[str, list[str]] = {
    "nfl_game_status_change": [
        "nfl_game_start",
        "nfl_game_result",
        "nfl_game_initialize",
    ],
    # Other event types (nfl_game_update, nfl_odds_update) are direct mappings
}


class NFLTrialParams(BaseModel):
    """Trial parameters for NFL scenario."""

    # NFL game configuration
    espn_game_id: str = Field(..., description="ESPN game ID (e.g., '401671827')")

    # Hub configuration (required)
    hub: HubConfig = Field(..., description="Hub configuration with persistence file")
    hub_id: str = Field(default="nfl_hub")

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
            "  - 'agent_config_path': str (optional) - Path to agent YAML config file"
        ),
    )

    # Search queries (optional, for triggering searches)
    # If not provided, will be auto-generated based on espn_game_id
    # Supports query templates with placeholders: {teams}, {home_team}, {away_team}, {date}, {home_abbrev}, {away_abbrev}, {week}
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
            "  - {home_abbrev} - Home team abbreviation (e.g., 'KC')\n"
            "  - {away_abbrev} - Away team abbreviation (e.g., 'SF')\n"
            "  - {week} - NFL week number"
        ),
    )


def _build_trial_spec(
    trial_id: str,
    params: NFLTrialParams,
) -> TrialSpec:
    """Return a :class:`TrialSpec` that wires DataHub, streams, and agents together."""

    # For NFL, we'll use the espn_game_id directly since NFL data comes from ESPN
    # Team info will be filled in from the store when the game initializes
    espn_game_id = params.espn_game_id
    home_team_abbreviation: str | None = None
    away_team_abbreviation: str | None = None
    home_team_name: str | None = None
    away_team_name: str | None = None
    game_date: str | None = None

    logger.info(
        "Building NFL trial for espn_game_id=%s (team info will be populated from store)",
        espn_game_id,
    )

    # Fallback: extract game_date from persistence_file path if not available
    # Path format: data/nfl-betting/YYYY-MM-DD/espn_game_id.jsonl
    if not game_date:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", params.hub.persistence_file)
        if date_match:
            game_date = date_match.group(1)
            logger.info("Extracted game_date from persistence_file path: %s", game_date)

    # Extract hub configuration
    hub_id = params.hub_id
    persistence_file = params.hub.persistence_file

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

            ds_stream_config: NFLPreGameBettingDataHubDataStreamConfig = {
                "actor_id": ds_config.id,
                "hub_id": hub_id,
                "persistence_file": persistence_file,
                "event_type": ds_config.event_type,
                "event_types": actual_event_types,
            }

            # Add optional fields
            if home_team_abbreviation:
                ds_stream_config["home_team_abbreviation"] = home_team_abbreviation
            if away_team_abbreviation:
                ds_stream_config["away_team_abbreviation"] = away_team_abbreviation

            # Handle initializer config for raw_web_search stream
            if ds_config.event_type == "raw_web_search":
                ds_stream_config["websearch_store_id"] = params.websearch_store_id
                if home_team_abbreviation:
                    ds_stream_config["home_team_abbreviation"] = home_team_abbreviation
                if away_team_abbreviation:
                    ds_stream_config["away_team_abbreviation"] = away_team_abbreviation
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
                actor_cls=NFLPreGameBettingDataHubDataStream,
                config=ds_stream_config,
            )
            stream_specs.append(stream_spec)
    else:
        # Fallback to flat event_types structure - create one stream per event type
        for event_type in event_types_list:
            flat_stream_config: NFLPreGameBettingDataHubDataStreamConfig = {
                "actor_id": f"{event_type}_stream",
                "hub_id": hub_id,
                "persistence_file": persistence_file,
                "event_type": event_type,
                "event_types": [event_type],
            }

            # Add optional fields
            if home_team_abbreviation:
                flat_stream_config["home_team_abbreviation"] = home_team_abbreviation
            if away_team_abbreviation:
                flat_stream_config["away_team_abbreviation"] = away_team_abbreviation

            # Handle initializer config for raw_web_search stream
            if event_type == "raw_web_search":
                flat_stream_config["websearch_store_id"] = params.websearch_store_id
                if home_team_abbreviation:
                    flat_stream_config["home_team_abbreviation"] = (
                        home_team_abbreviation
                    )
                if away_team_abbreviation:
                    flat_stream_config["away_team_abbreviation"] = (
                        away_team_abbreviation
                    )
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
                actor_cls=NFLPreGameBettingDataHubDataStream,
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
    # For agents with agent_config_path, expand to include all model-specific agent IDs
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
    # Agents with agent_config_path are expanded into multiple agents (one per model)
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

    # Build metadata with game information and hub config
    # This metadata is used by build_runtime_context and store factories
    metadata: dict[str, Any] = {
        "sample": "nfl-moneyline",
        "espn_game_id": params.espn_game_id,
        "hub_id": hub_id,
        "persistence_file": persistence_file,
        "event_types": params.event_types,
        # Store types to create (used by build_runtime_context)
        "store_types": ["nfl", "websearch"],
    }

    # Add team information if available
    if home_team_abbreviation and away_team_abbreviation:
        metadata["home_team_abbreviation"] = home_team_abbreviation
        metadata["away_team_abbreviation"] = away_team_abbreviation
        if game_date:
            metadata["game_date"] = game_date

    return TrialSpec(
        trial_id=trial_id,
        data_streams=tuple(stream_specs),
        operators=tuple(operator_specs),
        agents=tuple(agent_specs),
        metadata=metadata,
    )


def _build_nfl_runtime_context(spec: TrialSpec) -> RuntimeContext:
    """Build runtime context for NFL moneyline betting trial.

    Uses the generic build_runtime_context with registered store factories
    to create DataHub and store instances.

    Args:
        spec: Trial specification

    Returns:
        RuntimeContext with trial_id, data_hubs, stores, and startup callback
    """
    metadata = dict(spec.metadata)  # Convert to regular dict for type compatibility

    # Get hub configuration from metadata
    hub_id_raw = metadata.get("hub_id", "nfl_hub")
    hub_id = str(hub_id_raw) if hub_id_raw else "nfl_hub"

    persistence_file_raw = metadata.get("persistence_file")
    if not persistence_file_raw:
        raise ValueError("persistence_file is required in metadata")
    persistence_file = str(persistence_file_raw)

    # Get store types from metadata (defaults to NFL + websearch)
    store_types_raw = metadata.get("store_types", ["nfl", "websearch"])
    if isinstance(store_types_raw, list):
        store_types = [str(s) for s in store_types_raw]
    else:
        store_types = ["nfl", "websearch"]

    # Build and return RuntimeContext directly
    return build_runtime_context(
        trial_id=spec.trial_id,
        hub_id=hub_id,
        persistence_file=persistence_file,
        metadata=metadata,
        store_types=store_types,
        sport_type="nfl",
    )


register_trial_builder(
    "nfl",
    NFLTrialParams,
    _build_trial_spec,
    description="NFL betting scenario with relevant data inputs",
    context_builder=_build_nfl_runtime_context,
    example_params={
        "espn_game_id": "401671827",
        "hub": {
            "persistence_file": "outputs/nfl_events.jsonl",
        },
        "data_streams": [
            {
                "id": "raw_web_search_stream",
                "event_type": "raw_web_search",
                "initializer": {
                    "search_queries": [
                        {
                            "template": "NFL injury updates for {teams} on {date}",
                            "intent": "injury_summary",
                        },
                        {
                            "template": "NFL power rankings Week {week}",
                            "intent": "power_ranking",
                        },
                        {
                            "template": "NFL expert predictions for {teams}",
                            "intent": "expert_prediction",
                        },
                    ]
                },
            },
            {
                "id": "injury_summary_stream",
                "event_type": "injury_summary",
            },
            {
                "id": "power_ranking_stream",
                "event_type": "power_ranking",
            },
            {
                "id": "expert_prediction_stream",
                "event_type": "expert_prediction",
            },
            {
                "id": "nfl_game_status_change_stream",
                "event_type": "nfl_game_status_change",
            },
            {
                "id": "nfl_game_update_stream",
                "event_type": "nfl_game_update",
            },
            {
                "id": "nfl_odds_update_stream",
                "event_type": "nfl_odds_update",
            },
            {
                "id": "nfl_play_stream",
                "event_type": "nfl_play",
            },
        ],
        "operators": [
            {
                "id": "betting_broker",
                "class": "BrokerOperator",
                "initial_balance": "1000.00",
                "data_streams": [
                    "nfl_game_status_change_stream",
                    "nfl_odds_update_stream",
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
                    "nfl_game_update_stream",
                    "nfl_odds_update_stream",
                    "nfl_game_status_change_stream",
                    "nfl_play_stream",
                ],
            }
        ],
    },
)
