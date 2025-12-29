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
from agentx.data.nba import NBAExternalAPI, NBAStore
from agentx.data.polymarket import PolymarketAPI, PolymarketStore
from agentx.data.nba._utils import get_game_info_by_id
from agentx.data.websearch._processors import (
    ExpertPredictionProcessor,
    InjurySummaryProcessor,
    PowerRankingProcessor,
)
from agentx.nba_moneyline._agent import (
    NBABettingAgent,
    NBABettingAgentConfig,
)
from agentx.nba_moneyline._datastream import (
    NBAPreGameBettingDataHubDataStream,
    NBAPreGameBettingDataHubDataStreamConfig,
)
from agentx.data._models import EventTypes
from agentx.nba_moneyline._operator import (
    EventCounterOperator,
    EventCounterOperatorConfig,
)
from agentx.nba_moneyline._broker import (
    BrokerOperator,
    BrokerOperatorConfig,
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
    "game_status_change": [EventTypes.GAME_START.value, EventTypes.GAME_RESULT.value, EventTypes.GAME_INITIALIZE.value],
    # Other event types (game_update, odds_update) are direct mappings
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
    data_streams: list[str] = Field(default_factory=list, description="DataStream actor IDs to subscribe to")
    initial_balance: str | None = Field(default=None, description="Initial balance for broker (if applicable)")
    
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

    # Agent configuration
    agents: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "List of agent configurations. Each agent dict should have:\n"
            "  - 'id': str (required) - Agent identifier\n"
            "  - 'class': str (required) - Must be 'NBABettingAgent'\n"
            "  - 'operators': list[str] (optional) - Operator IDs to register\n"
            "  - 'data_streams': list[str] (optional) - DataStream actor IDs to subscribe to\n"
            "  - 'agent_config_path': str (optional) - Path to agent YAML config file"
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
        logger.info(
            "Found game info: %s on %s",
            f"{away_team_tricode} @ {home_team_tricode}",
            game_date,
        )
    else:
        logger.error(
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
    websearch_api = WebSearchAPI()
    websearch_store = WebSearchStore(
        store_id=params.websearch_store_id,
        api=websearch_api
    )
    
    # Setup NBAStore for game status events
    # Default intervals: scoreboard=5s, play_by_play=2s
    nba_api = NBAExternalAPI()
    nba_store = NBAStore(
        store_id="nba_store",
        api=nba_api,
        # poll_intervals will use defaults: {"scoreboard": 60.0, "play_by_play": 20.0}
    )
    
    # Setup PolymarketStore for odds updates
    # Default interval: odds=300s (5 minutes)
    polymarket_api = PolymarketAPI()
    polymarket_store = PolymarketStore(
        store_id="polymarket_store",
        api=polymarket_api,
        # poll_intervals will use defaults: {"odds": 300.0}
    )

    # Extract event_types from data_streams if provided, otherwise use event_types field
    if params.data_streams:
        event_types_list = [ds.event_type for ds in params.data_streams]
        logger.info(
            "Extracted event types from data_streams config: %s",
            event_types_list,
        )
    elif params.event_types:
        event_types_list = params.event_types
        logger.info(
            "Using event_types from params: %s",
            event_types_list,
        )
    else:
        # Default event types
        event_types_list = ["raw_web_search", "injury_summary", "power_ranking", "expert_prediction"]
        logger.info(
            "Using default event types: %s",
            event_types_list,
        )
    
    # Collect all event types including game events
    # Game events come from NBAStore and PolymarketStore
    all_event_types = set(event_types_list)
    
    # Expand synthetic event types to actual event types
    expanded_event_types = set()
    for event_type in all_event_types:
        if event_type in SYNTHETIC_EVENT_TYPE_MAP:
            expanded_event_types.update(SYNTHETIC_EVENT_TYPE_MAP[event_type])
        else:
            expanded_event_types.add(event_type)
    
    # Auto-register processors based on requested event_types
    # Use EVENT_TYPE_PROCESSOR_MAP to determine which processors are needed
    registered_event_types = set()
    for event_type in event_types_list:
        if event_type in EVENT_TYPE_PROCESSOR_MAP:
            processor_class, source_event_types = EVENT_TYPE_PROCESSOR_MAP[event_type]
            if event_type not in registered_event_types:
                processor = processor_class() if processor_class else None
                websearch_store.register_stream(event_type, processor, source_event_types)
                registered_event_types.add(event_type)
                logger.debug(
                    "Registered event_type '%s' with processor %s (sources: %s)",
                    event_type,
                    processor_class.__name__ if processor_class else "None",
                    source_event_types,
                )
        else:
            logger.warning(
                "Unknown event_type '%s' not in EVENT_TYPE_PROCESSOR_MAP, skipping processor registration",
                event_type,
            )

    # Connect stores to DataHub
    hub.connect_store(websearch_store)
    hub.connect_store(nba_store)
    hub.connect_store(polymarket_store)
    
    # Set up polling identifiers for game events
    # NBA store needs game_id to poll game status
    nba_store.set_poll_identifier({"game_id": params.game_id})
    # Polymarket store uses game_id for consistency (all events will use same event_id)
    polymarket_store.set_poll_identifier({"game_id": params.game_id})
    
    # Start polling on both stores (they will poll automatically)
    # Note: Stores start polling when DataHub connects them via set_event_emitter
    # The dashboard will call start_polling() during trial startup

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
            if home_team_tricode:
                ds_stream_config["home_team_tricode"] = home_team_tricode
            if away_team_tricode:
                ds_stream_config["away_team_tricode"] = away_team_tricode
            
            # Handle initializer config for raw_web_search stream
            if ds_config.event_type == "raw_web_search":
                ds_stream_config["websearch_store_id"] = params.websearch_store_id
                if home_team_tricode:
                    ds_stream_config["home_team_tricode"] = home_team_tricode
                if away_team_tricode:
                    ds_stream_config["away_team_tricode"] = away_team_tricode
                if home_team_name:
                    ds_stream_config["home_team_name"] = home_team_name
                if away_team_name:
                    ds_stream_config["away_team_name"] = away_team_name
                if game_date:
                    ds_stream_config["game_date"] = game_date
                # Get search_queries from initializer if provided
                if ds_config.initializer and "search_queries" in ds_config.initializer:
                    ds_stream_config["search_queries"] = ds_config.initializer["search_queries"]
            
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
            if home_team_tricode:
                flat_stream_config["home_team_tricode"] = home_team_tricode
            if away_team_tricode:
                flat_stream_config["away_team_tricode"] = away_team_tricode
            
            # Handle initializer config for raw_web_search stream
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
    operator_to_agents: dict[str, list[str]] = {}
    if params.agents:
        for agent_dict in params.agents:
            agent_id = agent_dict.get("id")
            if not agent_id:
                continue
            operator_ids = agent_dict.get("operators", [])
            for op_id in operator_ids:
                if op_id not in operator_to_agents:
                    operator_to_agents[op_id] = []
                operator_to_agents[op_id].append(str(agent_id))

    # Create operators - use hierarchical config if provided, otherwise create default
    operator_specs = []
    if params.operators:
        # Use hierarchical operators config
        operator_class_map = {
            "EventCounterOperator": EventCounterOperator,
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
                operator_config: BrokerOperatorConfig | EventCounterOperatorConfig = broker_config
            else:
                counter_config: EventCounterOperatorConfig = {"actor_id": op_config.id}
                operator_config = counter_config
            
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
    else:
        # Default: create event_counter operator
        default_op_config: EventCounterOperatorConfig = {"actor_id": "event_counter"}
        operator_spec = OperatorSpec(
            actor_id="event_counter",
            actor_cls=EventCounterOperator,
            config=default_op_config,
            agent_ids=tuple(operator_to_agents.get("event_counter", [])),
        )
        operator_specs.append(operator_spec)
        logger.info("Created event counter operator")

    # Create agent specs from agents config
    agent_specs = []
    
    if not params.agents:
        raise ValueError(
            "No agents specified. At least one agent with class 'NBABettingAgent' is required."
        )
    
    for agent_dict in params.agents:
        agent_id = agent_dict.get("id")
        if not agent_id:
            raise ValueError("Agent config missing required 'id' field")
        
        agent_class_name = agent_dict.get("class")
        if agent_class_name != "NBABettingAgent":
            raise ValueError(
                f"Invalid agent class '{agent_class_name}' for agent '{agent_id}'. "
                "Only 'NBABettingAgent' is supported."
            )
        
        operator_ids = agent_dict.get("operators", [])
        data_stream_ids = agent_dict.get("data_streams", [])
        
        # Create agent config - pass through config fields from agent_dict
        agent_config: NBABettingAgentConfig = {
            "actor_id": agent_id,
        }
        # Copy optional config fields
        if agent_dict.get("name"):
            agent_config["name"] = agent_dict["name"]
        if agent_dict.get("agent_config_path"):
            agent_config["agent_config_path"] = agent_dict["agent_config_path"]
        if agent_dict.get("model_type"):
            agent_config["model_type"] = agent_dict["model_type"]
        if agent_dict.get("model_name"):
            agent_config["model_name"] = agent_dict["model_name"]
        
        agent_spec = AgentSpec[NBABettingAgentConfig](
            actor_id=agent_id,
            actor_cls=NBABettingAgent,
            config=agent_config,
            operator_ids=tuple(operator_ids) if operator_ids else (),
            data_stream_ids=tuple(data_stream_ids),
        )
        agent_specs.append(agent_spec)

    # Build metadata with game information
    metadata: dict[str, Any] = {
        "sample": "nba-pregame-betting",
        "game_id": params.game_id,
        "hub_id": params.hub_id,
        "event_types": params.event_types,
    }
    
    # Add market_url if provided
    if params.market_url:
        metadata["market_url"] = params.market_url
    
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
    
    # Create Store instances
    # WebSearchStore
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
                config = stream_spec.config
                # Check both event_type (singular) and event_types (plural)
                event_types_to_check = []
                if config.get("event_type"):
                    event_types_to_check.append(config.get("event_type"))
                if config.get("event_types"):
                    event_types_to_check.extend(config.get("event_types", []))
                
                for event_type in event_types_to_check:
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
    
    # Create NBAStore for game status events
    # Default intervals: scoreboard=5s, play_by_play=2s
    if "nba_store" not in context["stores"]:
        game_id = spec.metadata.get("game_id", "")
        nba_api = NBAExternalAPI()
        nba_store = NBAStore(
            store_id="nba_store",
            api=nba_api,
            # poll_intervals will use defaults: {"scoreboard": 5.0, "play_by_play": 2.0}
        )
        nba_store.set_poll_identifier({"game_id": game_id})
        # Connect to hub
        hub_id = list(context["data_hubs"].keys())[0] if context["data_hubs"] else None
        if hub_id:
            context["data_hubs"][hub_id].connect_store(nba_store)
        context["stores"]["nba_store"] = nba_store
    
    # Create PolymarketStore for odds updates
    # Default interval: odds=300s (5 minutes)
    if "polymarket_store" not in context["stores"]:
        game_id = spec.metadata.get("game_id", "")
        market_url_raw = spec.metadata.get("market_url")
        market_url: str | None = market_url_raw if isinstance(market_url_raw, str) else None
        
        # Prepare identifier for polling (will be used if market_url/slug not available)
        # Use game_id for consistency (all events will use same event_id)
        identifier: dict[str, Any] = {"game_id": game_id}
        
        # If market_url not provided, try to construct slug from game info
        if not market_url:
            away_tricode_raw = spec.metadata.get("away_team_tricode")
            home_tricode_raw = spec.metadata.get("home_team_tricode")
            game_date_raw = spec.metadata.get("game_date")
            away_tricode = away_tricode_raw if isinstance(away_tricode_raw, str) else None
            home_tricode = home_tricode_raw if isinstance(home_tricode_raw, str) else None
            game_date = game_date_raw if isinstance(game_date_raw, str) else None
            if away_tricode and home_tricode and game_date:
                identifier["away_tricode"] = away_tricode
                identifier["home_tricode"] = home_tricode
                identifier["game_date"] = game_date
        
        polymarket_api = PolymarketAPI()
        polymarket_store = PolymarketStore(
            store_id="polymarket_store",
            api=polymarket_api,
            # poll_intervals will use defaults: {"odds": 300.0}
            market_url=market_url,
        )
        polymarket_store.set_poll_identifier(identifier)
        # Connect to hub
        hub_id = list(context["data_hubs"].keys())[0] if context["data_hubs"] else None
        if hub_id:
            context["data_hubs"][hub_id].connect_store(polymarket_store)
        context["stores"]["polymarket_store"] = polymarket_store
    
    # Start all stores after they're all connected and configured
    # Add startup function to context that dashboard can call
    async def start_data_stores() -> None:
        """Start all DataHub stores (begin polling)."""
        hub_id = list(context["data_hubs"].keys())[0] if context["data_hubs"] else None
        if hub_id:
            hub = context["data_hubs"][hub_id]
            await hub.start()
    
    context["_startup"] = start_data_stores
    
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
            {
                "id": "game_status_change_stream",
                "event_type": "game_status_change",
            },
            {
                "id": "game_update_stream",
                "event_type": "game_update",
            },
            {
                "id": "odds_update_stream",
                "event_type": "odds_update",
            },
            {
                "id": "play_by_play_stream",
                "event_type": "play_by_play",
            },
        ],
        "operators": [
            {
                "id": "event_counter",
                "class": "EventCounterOperator",
            },
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
                "class": "NBABettingAgent",
                "operators": ["event_counter", "betting_broker"],
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
