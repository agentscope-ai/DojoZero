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


class NBAPreGameBettingTrialParams(BaseModel):
    """Trial parameters for NBA pre-game betting scenario."""

    # NBA game configuration
    game_id: str = Field(..., description="NBA.com game ID (e.g., '0022500290')")

    # DataHub configuration
    hub_id: str = Field(default="nba_pregame_hub")
    persistence_file: str = Field(default="outputs/nba_pregame_events.jsonl")
    enable_persistence: bool = Field(default=True)

    # Store configuration
    websearch_store_id: str = Field(default="websearch_store")
    poll_interval_seconds: float = Field(default=30.0)

    # Stream configuration
    stream_ids: list[str] = Field(
        default_factory=lambda: [
            "raw_web_search",
            "injury_summary",
            "power_ranking",
            "expert_prediction",
        ]
    )

    # Agent configuration
    agent_ids: list[str] = Field(default_factory=lambda: ["betting_agent"])

    # Search queries (optional, for triggering searches)
    # If not provided, will be auto-generated based on game_id
    search_queries: list[dict[str, Any]] = Field(default_factory=list)


def _build_trial_spec(
    trial_id: str,
    params: NBAPreGameBettingTrialParams,
) -> TrialSpec:
    """Return a :class:`TrialSpec` that wires DataHub, streams, and agents together."""

    # Get game information from game_id to extract team tricodes
    game_info = get_game_info_by_id(params.game_id)
    home_team_tricode: str | None = None
    away_team_tricode: str | None = None
    game_date: str | None = None

    if game_info:
        home_team_tricode = game_info.get("home_team_tricode")
        away_team_tricode = game_info.get("away_team_tricode")
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

    # Create DataHub instance
    hub = DataHub(
        hub_id=params.hub_id,
        persistence_file=params.persistence_file,
        enable_persistence=params.enable_persistence,
    )

    # Setup WebSearchStore
    api = WebSearchAPI()
    store = WebSearchStore(
        store_id=params.websearch_store_id,
        api=api
    )

    # Register processors
    store.register_stream("injury_summary", InjurySummaryProcessor(), ["raw_web_search"])
    store.register_stream("power_ranking", PowerRankingProcessor(), ["raw_web_search"])
    store.register_stream(
        "expert_prediction", ExpertPredictionProcessor(), ["raw_web_search"]
    )

    # Connect store to DataHub
    hub.connect_store(store)

    # Create stream specs - one per stream_id
    stream_specs = []
    for stream_id in params.stream_ids:
        # Determine event_types for this stream
        if stream_id == "raw_web_search":
            event_types = ["raw_web_search"]
        else:
            # Processed streams produce events with matching event_type
            event_types = [stream_id]

        stream_config: NBAPreGameBettingDataHubDataStreamConfig = {
            "actor_id": f"{stream_id}_stream",
            "hub_id": params.hub_id,
            "persistence_file": params.persistence_file,
            "stream_id": stream_id,
            "event_types": event_types,
        }

        # Store hub and store references in registry so from_dict can access them
        # This is a workaround - in production, hub would be in runtime context
        if not hasattr(NBAPreGameBettingDataHubDataStream, "_hub_registry"):
            setattr(NBAPreGameBettingDataHubDataStream, "_hub_registry", {})
        if not hasattr(NBAPreGameBettingDataHubDataStream, "_store_registry"):
            setattr(NBAPreGameBettingDataHubDataStream, "_store_registry", {})
        
        stream_hub_registry: dict[str, DataHub] = getattr(NBAPreGameBettingDataHubDataStream, "_hub_registry", {})
        stream_store_registry: dict[str, WebSearchStore] = getattr(NBAPreGameBettingDataHubDataStream, "_store_registry", {})
        stream_hub_registry[params.hub_id] = hub
        stream_store_registry[params.websearch_store_id] = store

        # Add team metadata and store reference for raw_web_search stream
        if stream_id == "raw_web_search" and home_team_tricode and away_team_tricode:
            stream_config["websearch_store_id"] = params.websearch_store_id
            stream_config["home_team_tricode"] = home_team_tricode
            stream_config["away_team_tricode"] = away_team_tricode
            if game_date:
                stream_config["game_date"] = game_date

        stream_spec = DataStreamSpec(
            actor_id=f"{stream_id}_stream",
            actor_cls=NBAPreGameBettingDataHubDataStream,
            config=stream_config,
            consumer_ids=tuple(params.agent_ids),
        )
        stream_specs.append(stream_spec)

    # Create counter operator for tracking events
    operator_specs = []
    operator_config: EventCounterOperatorConfig = {"actor_id": "event_counter"}
    operator_spec = OperatorSpec(
        actor_id="event_counter",
        actor_cls=EventCounterOperator,
        config=operator_config,
    )
    operator_specs.append(operator_spec)
    LOGGER.info("Created event counter operator")

    # Create agent specs
    agent_specs = []
    for agent_id in params.agent_ids:
        agent_config: DummyAgentConfig = {
            "actor_id": agent_id,
            "operator_id": "event_counter",
        }
        agent_spec = AgentSpec[DummyAgentConfig](
            actor_id=agent_id,
            actor_cls=DummyAgent,
            config=agent_config,
            operator_ids=("event_counter",),
        )
        agent_specs.append(agent_spec)

    # Build metadata with game information
    metadata: dict[str, Any] = {
        "sample": "nba-pregame-betting",
        "game_id": params.game_id,
        "hub_id": params.hub_id,
        "stream_ids": params.stream_ids,
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


register_trial_builder(
    "nba-pregame-betting",
    NBAPreGameBettingTrialParams,
    _build_trial_spec,
    description="NBA pre-game betting scenario with relevant data inputs",
    example_params=NBAPreGameBettingTrialParams(
        game_id="0022501205",  # Example NBA game ID
        hub_id="nba_pregame_hub",
        persistence_file="outputs/nba_pregame_events.jsonl",
        stream_ids=["raw_web_search", "injury_summary", "power_ranking"],
        agent_ids=["betting_agent"],
    ),
)
