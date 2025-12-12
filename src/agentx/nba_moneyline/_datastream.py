"""NBA pre-game betting DataStream with search initialization."""

import logging
from typing import Any, Mapping, TypedDict

from agentx.data import DataHub, WebSearchStore
from agentx.data._streams import DataHubDataStream as BaseDataHubDataStream
from agentx.nba_moneyline._initializer import NBAStreamInitializer

LOGGER = logging.getLogger("agentx.nba_moneyline.datastream")


class _ActorIdConfig(TypedDict):
    actor_id: str


class NBAPreGameBettingDataHubDataStreamConfig(_ActorIdConfig, total=False):
    """Configuration for NBA pre-game betting DataHubDataStream."""
    hub_id: str
    persistence_file: str
    stream_id: str  # Which stream_id to subscribe to in DataHub
    event_types: list[str]  # Which event_types to subscribe to (alternative to stream_id)
    websearch_store_id: str  # Store ID for triggering searches (only for raw_web_search stream)
    home_team_tricode: str  # Team metadata for generating queries
    away_team_tricode: str
    home_team_name: str  # Full team name for search queries
    away_team_name: str
    game_date: str


class NBAPreGameBettingDataHubDataStream(
    BaseDataHubDataStream
):
    """NBA pre-game betting DataStream that extends generic DataHubDataStream.
    
    Adds NBA-specific initialization logic (triggering web searches) via
    NBAStreamInitializer.
    """

    def __init__(
        self,
        *,
        actor_id: str,
        hub: DataHub | None = None,
        stream_id: str | None = None,
        event_types: list[str] | None = None,
        store: WebSearchStore | None = None,
        home_team_tricode: str | None = None,
        away_team_tricode: str | None = None,
        home_team_name: str | None = None,
        away_team_name: str | None = None,
        game_date: str | None = None,
    ) -> None:
        # Create initializer if store and team names are provided
        initializer: NBAStreamInitializer | None = None
        if store and home_team_name and away_team_name:
            initializer = NBAStreamInitializer(
                store=store,
                home_team_name=home_team_name,
                away_team_name=away_team_name,
                game_date=game_date,
            )
        
        super().__init__(
            actor_id=actor_id,
            hub=hub,
            stream_id=stream_id,
            event_types=event_types,
            initializer=initializer,
        )

    @classmethod
    def from_dict(
        cls,
        config: NBAPreGameBettingDataHubDataStreamConfig,
        context: dict[str, Any] | None = None,
    ) -> "NBAPreGameBettingDataHubDataStream":
        # Get hub and store from context (provided by dashboard during materialization)
        hub: DataHub | None = None
        store: WebSearchStore | None = None
        
        if context and "data_hubs" in context:
            hub_id = config.get("hub_id", "default_hub")
            hub = context["data_hubs"].get(hub_id)
        
        if context and "stores" in context:
            store_id = config.get("websearch_store_id")
            if store_id:
                store = context["stores"].get(store_id)

        if hub is None:
            # Fallback: create new hub (shouldn't happen in normal flow)
            hub_id = config.get("hub_id", "default_hub")
            persistence_file = config.get("persistence_file", "outputs/events.jsonl")
            hub = DataHub(hub_id=hub_id, persistence_file=persistence_file)

        return cls(
            actor_id=config["actor_id"],
            hub=hub,
            stream_id=config.get("stream_id"),
            event_types=config.get("event_types", []),
            store=store,
            home_team_tricode=config.get("home_team_tricode"),
            away_team_tricode=config.get("away_team_tricode"),
            home_team_name=config.get("home_team_name"),
            away_team_name=config.get("away_team_name"),
            game_date=config.get("game_date"),
        )
