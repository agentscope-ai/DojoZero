"""Trial builder for NBA pre-game betting scenario."""

import logging
from typing import Any

from pydantic import BaseModel, Field

from agentx.core import (
    AgentSpec,
    DataStreamSpec,
    OperatorSpec,
    register_trial_builder,
    TrialSpec,
)
from agentx.data import DataHub, WebSearchAPI, WebSearchStore
from agentx.data.nba._utils import get_game_info_by_id
from agentx.data.websearch._processors import (
    ExpertPredictionProcessor,
    InjurySummaryProcessor,
    PowerRankingProcessor,
)
from agentx.nba_moneyline._agent import (
    DummyAgent,
    DummyAgentConfig,
)
from agentx.nba_moneyline._datastream import (
    NBAPreGameBettingDataHubDataStream,
    NBAPreGameBettingDataHubDataStreamConfig,
)
from agentx.nba_moneyline._operator import (
    EventCounterOperator,
    EventCounterOperatorConfig,
)

LOGGER = logging.getLogger("agentx.nba_moneyline.trial")

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


class HubConfig(BaseModel):
    """Hub configuration."""
    persistence_file: str = Field(default="outputs/nba_pregame_events.jsonl")
    enable_persistence: bool = Field(default=True)


class DataStreamConfig(BaseModel):
    """Data stream configuration."""
    id: str
    event_type: str
    initializer: dict[str, Any] | None = Field(default=None)


class OperatorConfig(BaseModel):
    """Operator configuration."""
    id: str
    class_name: str = Field(alias="class", description="Operator class name")
    
    class Config:
        populate_by_name = True


class NBAPreGameBettingTrialParams(BaseModel):
    """Trial parameters for NBA pre-game betting scenario."""

    # NBA game configuration
    game_id: str = Field(..., description="NBA.com game ID (e.g., '0022500290')")

    # Hub configuration (optional, can be nested or flat)
    hub: HubConfig | None = Field(default=None)
    hub_id: str = Field(default="nba_pregame_hub")
    persistence_file: str | None = Field(default=None)
    enable_persistence: bool | None = Field(default=None)

    # Store configuration
    websearch_store_id: str = Field(default="websearch_store")
    poll_interval_seconds: float = Field(default=30.0)

    # Data streams configuration (optional, hierarchical)
    data_streams: list[DataStreamConfig] | None = Field(default=None)
    
    # Event type configuration (which event types to create streams for) - used if data_streams not provided
    event_types: list[str] | None = Field(
        default=None,
        description="List of event types to create streams for (used if data_streams not provided)"
    )
    
    # Operators configuration (optional, hierarchical)
    operators: list[OperatorConfig] | None = Field(default=None)

    # Agent configuration (new structure with full agent configs)
    agents: list[dict[str, Any]] = Field(
        default_factory=lambda: [
            {
                "id": "betting_agent",
                "class": "DummyAgent",
                "operators": ["event_counter"],
                "data_streams": [],
            }
        ],
        description=(
            "List of agent configurations. Each agent dict should have:\n"
            "  - 'id': str (required) - Agent identifier\n"
            "  - 'class': str (required) - Agent class name (e.g., 'DummyAgent')\n"
            "  - 'operators': list[str] (optional) - Operator IDs to register\n"
            "  - 'data_streams': list[str] (optional) - DataStream actor IDs to subscribe to"
        ),
    )
    
    # Legacy: agent_ids for backward compatibility (deprecated)
    agent_ids: list[str] | None = Field(
        default=None,
        description="Deprecated: Use 'agents' config instead. If provided, creates simple agents.",
    )

    # Search queries (optional, for triggering searches)
    # If not provided, will be auto-generated based on game_id
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


def _build_trial_spec(
    trial_id: str,
    params: NBAPreGameBettingTrialParams,
) -> TrialSpec:
    """Return a :class:`TrialSpec` that wires DataHub, streams, and agents together."""

    # Get game information from game_id to extract team tricodes and names
    game_info = get_game_info_by_id(params.game_id)
    home_team_tricode: str | None = None
    away_team_tricode: str | None = None
    home_team_name: str | None = None
    away_team_name: str | None = None
    game_date: str | None = None

    if game_info:
        home_team_tricode = game_info.get("home_team_tricode")
        away_team_tricode = game_info.get("away_team_tricode")
        home_team_name = game_info.get("home_team")
        away_team_name = game_info.get("away_team")
        game_date = game_info.get("game_date")
        LOGGER.info(
            "Found game info: %s @ %s on %s",
            f"{away_team_tricode} @ {home_team_tricode}",
            game_date,
        )
    else:
        LOGGER.error(
            "Could not find game info for game_id=%s. Exiting.",
            params.game_id,
        )
        raise ValueError(f"Could not find game info for game_id={params.game_id}.")

    # Extract hub configuration (support both hierarchical and flat)
    if params.hub:
        hub_id = params.hub_id
        persistence_file = params.hub.persistence_file
        enable_persistence = params.hub.enable_persistence
    else:
        hub_id = params.hub_id
        persistence_file = params.persistence_file or "outputs/nba_pregame_events.jsonl"
        enable_persistence = params.enable_persistence if params.enable_persistence is not None else True
    
    # Create DataHub instance
    hub = DataHub(
        hub_id=hub_id,
        persistence_file=persistence_file,
        enable_persistence=enable_persistence,
    )

    # Setup WebSearchStore
    api = WebSearchAPI()
    store = WebSearchStore(
        store_id=params.websearch_store_id,
        api=api
    )

    # Extract event_types from data_streams if provided, otherwise use event_types field
    if params.data_streams:
        event_types_list = [ds.event_type for ds in params.data_streams]
    elif params.event_types:
        event_types_list = params.event_types
    else:
        # Default event types
        event_types_list = ["raw_web_search", "injury_summary", "power_ranking", "expert_prediction"]
    
    # Auto-register processors based on requested event_types
    # Use EVENT_TYPE_PROCESSOR_MAP to determine which processors are needed
    registered_event_types = set()
    for event_type in event_types_list:
        if event_type in EVENT_TYPE_PROCESSOR_MAP:
            processor_class, source_event_types = EVENT_TYPE_PROCESSOR_MAP[event_type]
            if event_type not in registered_event_types:
                processor = processor_class() if processor_class else None
                store.register_stream(event_type, processor, source_event_types)
                registered_event_types.add(event_type)
                LOGGER.debug(
                    "Registered event_type '%s' with processor %s (sources: %s)",
                    event_type,
                    processor_class.__name__ if processor_class else "None",
                    source_event_types,
                )
        else:
            LOGGER.warning(
                "Unknown event_type '%s' not in EVENT_TYPE_PROCESSOR_MAP, skipping processor registration",
                event_type,
            )

    # Connect store to DataHub
    hub.connect_store(store)

    # Create stream specs - use data_streams if provided, otherwise create from event_types
    stream_specs = []
    if params.data_streams:
        # Use hierarchical data_streams config
        for ds_config in params.data_streams:
            stream_config: NBAPreGameBettingDataHubDataStreamConfig = {
                "actor_id": ds_config.id,
                "hub_id": hub_id,
                "persistence_file": persistence_file,
                "event_type": ds_config.event_type,
                "event_types": [ds_config.event_type],
            }
            
            # Add optional fields
            if home_team_tricode:
                stream_config["home_team_tricode"] = home_team_tricode
            if away_team_tricode:
                stream_config["away_team_tricode"] = away_team_tricode
            
            # Handle initializer config for raw_web_search stream
            if ds_config.event_type == "raw_web_search":
                stream_config["websearch_store_id"] = params.websearch_store_id
                if home_team_tricode:
                    stream_config["home_team_tricode"] = home_team_tricode
                if away_team_tricode:
                    stream_config["away_team_tricode"] = away_team_tricode
                if home_team_name:
                    stream_config["home_team_name"] = home_team_name
                if away_team_name:
                    stream_config["away_team_name"] = away_team_name
                if game_date:
                    stream_config["game_date"] = game_date
                # Get search_queries from initializer if provided
                if ds_config.initializer and "search_queries" in ds_config.initializer:
                    stream_config["search_queries"] = ds_config.initializer["search_queries"]
            
            stream_spec = DataStreamSpec(
                actor_id=ds_config.id,
                actor_cls=NBAPreGameBettingDataHubDataStream,
                config=stream_config,
            )
            stream_specs.append(stream_spec)
    else:
        # Fallback to flat event_types structure
        for event_type in event_types_list:
            # Each stream subscribes to its matching event_type
            flat_stream_config: NBAPreGameBettingDataHubDataStreamConfig = {
                "actor_id": f"{event_type}_stream",
                "hub_id": hub_id,
                "persistence_file": persistence_file,
                "event_type": event_type,
                "event_types": [event_type],  # Subscribe to this event_type
            }
        
            # Add optional fields only if they have values
            if home_team_tricode:
                flat_stream_config["home_team_tricode"] = home_team_tricode
            if away_team_tricode:
                flat_stream_config["away_team_tricode"] = away_team_tricode

            # Add team metadata and store reference for raw_web_search stream
            if event_type == "raw_web_search":
                flat_stream_config["websearch_store_id"] = params.websearch_store_id
                if home_team_tricode:
                    flat_stream_config["home_team_tricode"] = home_team_tricode
                if away_team_tricode:
                    flat_stream_config["away_team_tricode"] = away_team_tricode
                if home_team_name:
                    flat_stream_config["home_team_name"] = home_team_name
                if away_team_name:
                    flat_stream_config["away_team_name"] = away_team_name
                if game_date:
                    flat_stream_config["game_date"] = game_date
                # Pass search_queries if provided (allows custom queries or auto-generation)
                if params.search_queries:
                    flat_stream_config["search_queries"] = params.search_queries

            stream_spec = DataStreamSpec(
                actor_id=f"{event_type}_stream",
                actor_cls=NBAPreGameBettingDataHubDataStream,
                config=flat_stream_config,
                # consumer_ids not set - using agent-centric wiring instead
            )
            stream_specs.append(stream_spec)

    # Create operators - use hierarchical config if provided, otherwise create default
    operator_specs = []
    if params.operators:
        # Use hierarchical operators config
        operator_class_map = {
            "EventCounterOperator": EventCounterOperator,
        }
        for op_config in params.operators:
            op_cls = operator_class_map.get(op_config.class_name)
            if op_cls is None:
                raise ValueError(f"Unknown operator class: {op_config.class_name}")
            op_config_dict: EventCounterOperatorConfig = {"actor_id": op_config.id}
            operator_spec = OperatorSpec(
                actor_id=op_config.id,
                actor_cls=op_cls,
                config=op_config_dict,
            )
            operator_specs.append(operator_spec)
            LOGGER.info("Created operator '%s' of class '%s'", op_config.id, op_config.class_name)
    else:
        # Default: create event_counter operator
        default_op_config: EventCounterOperatorConfig = {"actor_id": "event_counter"}
        operator_spec = OperatorSpec(
            actor_id="event_counter",
            actor_cls=EventCounterOperator,
            config=default_op_config,
        )
        operator_specs.append(operator_spec)
        LOGGER.info("Created event counter operator")

    # Create agent specs from agents config (agent-centric)
    agent_specs = []
    
    # Support both new agents config and legacy agent_ids
    if params.agents:
        # New structure: agents config with full agent definitions
        for agent_dict in params.agents:
            agent_id = agent_dict.get("id")
            if not agent_id:
                raise ValueError("Agent config missing required 'id' field")
            
            agent_class_name = agent_dict.get("class", "DummyAgent")
            operator_ids = agent_dict.get("operators", [])
            data_stream_ids = agent_dict.get("data_streams", [])
            
            # Map class name to class
            agent_class_map = {
                "DummyAgent": DummyAgent,
            }
            agent_cls = agent_class_map.get(agent_class_name)
            if agent_cls is None:
                raise ValueError(f"Unknown agent class: {agent_class_name}")
            
            # Create agent config dict
            new_agent_config: DummyAgentConfig = {
                "actor_id": agent_id,
                "operator_id": operator_ids[0] if operator_ids else "event_counter",
            }
            
            agent_spec = AgentSpec[DummyAgentConfig](
                actor_id=agent_id,
                actor_cls=agent_cls,
                config=new_agent_config,
                operator_ids=tuple(operator_ids) if operator_ids else ("event_counter",),
                data_stream_ids=tuple(data_stream_ids),
            )
            agent_specs.append(agent_spec)
    elif params.agent_ids:
        # Legacy: simple agent_ids list
        for agent_id in params.agent_ids:
            legacy_agent_config: DummyAgentConfig = {
                "actor_id": agent_id,
                "operator_id": "event_counter",
            }
            # Infer data_stream_ids from all created streams
            all_stream_ids = [f"{et}_stream" for et in event_types_list]
            agent_spec = AgentSpec[DummyAgentConfig](
                actor_id=agent_id,
                actor_cls=DummyAgent,
                config=legacy_agent_config,
                operator_ids=("event_counter",),
                data_stream_ids=tuple(all_stream_ids),
            )
            agent_specs.append(agent_spec)
    else:
        # Default: create one agent
        default_agent_config: DummyAgentConfig = {
            "actor_id": "betting_agent",
            "operator_id": "event_counter",
        }
        all_stream_ids = [f"{et}_stream" for et in event_types_list]
        agent_spec = AgentSpec[DummyAgentConfig](
            actor_id="betting_agent",
            actor_cls=DummyAgent,
            config=default_agent_config,
            operator_ids=("event_counter",),
            data_stream_ids=tuple(all_stream_ids),
        )
        agent_specs.append(agent_spec)

    # Build metadata with game information
    metadata: dict[str, Any] = {
        "sample": "nba-pregame-betting",
        "game_id": params.game_id,
        "hub_id": params.hub_id,
        "event_types": params.event_types,
    }
    
    # Add team information if available
    if home_team_tricode and away_team_tricode:
        metadata["home_team_tricode"] = home_team_tricode
        metadata["away_team_tricode"] = away_team_tricode
        if game_date:
            metadata["game_date"] = game_date

    return TrialSpec(
        trial_id=trial_id,
        data_streams=tuple(stream_specs),
        operators=tuple(operator_specs),
        agents=tuple(agent_specs),
        metadata=metadata,
    )


def _build_nba_runtime_context(spec: TrialSpec) -> dict[str, Any]:
    """Build runtime context for NBA pre-game betting trial.
    
    Creates DataHub and WebSearchStore instances from stream configs.
    This allows from_dict() methods to access these dependencies via context.
    
    Args:
        spec: Trial specification
        
    Returns:
        Context dictionary with 'data_hubs' and 'stores' keys
    """
    context: dict[str, Any] = {
        "data_hubs": {},
        "stores": {},
    }
    
    # Extract hub/store info from stream configs
    hub_configs: dict[str, dict[str, Any]] = {}
    store_configs: dict[str, dict[str, Any]] = {}
    
    for stream_spec in spec.data_streams:
        config = stream_spec.config
        hub_id = config.get("hub_id")
        persistence_file = config.get("persistence_file")
        websearch_store_id = config.get("websearch_store_id")
        
        if hub_id and persistence_file:
            if hub_id not in hub_configs:
                hub_configs[hub_id] = {
                    "hub_id": hub_id,
                    "persistence_file": persistence_file,
                    "enable_persistence": config.get("enable_persistence", True),
                }
        
        if websearch_store_id:
            if websearch_store_id not in store_configs:
                store_configs[websearch_store_id] = {
                    "store_id": websearch_store_id,
                }
    
    # Create DataHub instances
    for hub_id, hub_config in hub_configs.items():
        if hub_id not in context["data_hubs"]:
            hub = DataHub(
                hub_id=hub_config["hub_id"],
                persistence_file=hub_config["persistence_file"],
                enable_persistence=hub_config.get("enable_persistence", True),
            )
            context["data_hubs"][hub_id] = hub
    
    # Create Store instances (WebSearchStore for NBA trial)
    for store_id, store_config in store_configs.items():
        if store_id not in context["stores"]:
            api = WebSearchAPI()
            store = WebSearchStore(
                store_id=store_config["store_id"],
                api=api,
            )
            # Auto-register processors based on event_types found in stream specs
            # Use EVENT_TYPE_PROCESSOR_MAP to determine which processors are needed
            registered_event_types = set()
            for stream_spec in spec.data_streams:
                event_type = stream_spec.config.get("event_type")
                if event_type and event_type in EVENT_TYPE_PROCESSOR_MAP:
                    processor_class, source_event_types = EVENT_TYPE_PROCESSOR_MAP[event_type]
                    if event_type not in registered_event_types:
                        processor = processor_class() if processor_class else None
                        store.register_stream(event_type, processor, source_event_types)
                        registered_event_types.add(event_type)
            # Connect store to hub
            hub_id = None
            for stream_spec in spec.data_streams:
                if stream_spec.config.get("websearch_store_id") == store_id:
                    hub_id = stream_spec.config.get("hub_id")
                    break
            if hub_id and hub_id in context["data_hubs"]:
                context["data_hubs"][hub_id].connect_store(store)
            context["stores"][store_id] = store
    
    return context


register_trial_builder(
    "nba-pregame-betting",
    NBAPreGameBettingTrialParams,
    _build_trial_spec,
    description="NBA pre-game betting scenario with relevant data inputs",
    context_builder=_build_nba_runtime_context,
    # Use dict format to match the hierarchical YAML structure in nba-pregame-betting_example_1.yaml
    example_params={
        "game_id": "0022501215",
        "hub": {
            "persistence_file": "outputs/nba_pregame_events.jsonl",
            "enable_persistence": True,
        },
        "data_streams": [
            {
                "id": "raw_web_search_stream",
                "event_type": "raw_web_search",
                "initializer": {
                    "search_queries": [
                        {"template": "NBA injury updates for {teams} on {date}", "intent": "injury_summary"},
                        {"template": "NBA power rankings", "intent": "power_ranking"},
                        {"template": "NBA expert predictions for {teams}", "intent": "expert_prediction"},
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
        ],
        "operators": [
            {
                "id": "event_counter",
                "class": "EventCounterOperator",
            }
        ],
        "agents": [
            {
                "id": "betting_agent",
                "class": "DummyAgent",
                "operators": ["event_counter"],
                "data_streams": [
                    "injury_summary_stream",
                    "power_ranking_stream",
                    "expert_prediction_stream",
                ],
            }
        ],
    },
)
