"""NBA pre-game betting DataStream with search initialization."""

import logging
from typing import Any, TypedDict

from dojozero.core import RuntimeContext
from dojozero.data import DataHub, WebSearchStore
from dojozero.data._streams import DataHubDataStream as BaseDataHubDataStream
from dojozero.nba._initializer import NBAStreamInitializer

logger = logging.getLogger(__name__)


class _ActorIdConfig(TypedDict):
    actor_id: str


class NBAPreGameBettingDataHubDataStreamConfig(_ActorIdConfig, total=False):
    """Configuration for NBA pre-game betting DataHubDataStream."""

    hub_id: str
    persistence_file: str
    enable_persistence: bool
    event_type: str  # Which event_type to subscribe to in DataHub
    event_types: list[
        str
    ]  # Which event_types to subscribe to (alternative to event_type)
    websearch_store_id: (
        str  # Store ID for triggering searches (only for raw_web_search stream)
    )
    home_team_tricode: str  # Team metadata for generating queries
    away_team_tricode: str
    home_team_name: str  # Full team name for search queries
    away_team_name: str
    game_date: str
    search_queries: list[
        dict[str, Any]
    ]  # Custom search queries (only for raw_web_search stream)


class NBAPreGameBettingDataHubDataStream(BaseDataHubDataStream):
    """NBA pre-game betting DataStream that extends generic DataHubDataStream.

    Adds NBA-specific initialization logic (triggering web searches) via
    NBAStreamInitializer.
    """

    def __init__(
        self,
        *,
        actor_id: str,
        trial_id: str,
        hub: DataHub | None = None,
        event_type: str | None = None,
        event_types: list[str] | None = None,
        store: WebSearchStore | None = None,
        home_team_tricode: str | None = None,
        away_team_tricode: str | None = None,
        home_team_name: str | None = None,
        away_team_name: str | None = None,
        game_date: str | None = None,
        search_queries: list[dict[str, Any]] | None = None,
        sport_type: str = "",
    ) -> None:
        # Create initializer if store is provided (team names or search_queries required)
        initializer: NBAStreamInitializer | None = None
        if store and (search_queries or (home_team_name and away_team_name)):
            initializer = NBAStreamInitializer(
                store=store,
                home_team_name=home_team_name,
                away_team_name=away_team_name,
                game_date=game_date,
                home_team_tricode=home_team_tricode,
                away_team_tricode=away_team_tricode,
                search_queries=search_queries,
            )

        super().__init__(
            actor_id=actor_id,
            trial_id=trial_id,
            hub=hub,
            event_type=event_type,
            event_types=event_types,
            initializer=initializer,
            sport_type=sport_type,
        )

    @classmethod
    def from_dict(
        cls,
        config: NBAPreGameBettingDataHubDataStreamConfig,
        context: RuntimeContext,
    ) -> "NBAPreGameBettingDataHubDataStream":
        # Get hub and store from context (provided by dashboard during materialization)
        hub: DataHub | None = None
        store: WebSearchStore | None = None

        hub_id = config.get("hub_id", "default_hub")
        hub = context.data_hubs.get(hub_id)

        store_id = config.get("websearch_store_id")
        if store_id:
            store = context.stores.get(store_id)

        if hub is None:
            # Fallback: create new hub (shouldn't happen in normal flow)
            persistence_file = config.get("persistence_file", "outputs/events.jsonl")
            hub = DataHub(hub_id=hub_id, persistence_file=persistence_file)

        return cls(
            actor_id=config["actor_id"],
            trial_id=context.trial_id,
            hub=hub,
            event_type=config.get("event_type"),
            event_types=config.get("event_types", []),
            store=store,
            home_team_tricode=config.get("home_team_tricode"),
            away_team_tricode=config.get("away_team_tricode"),
            home_team_name=config.get("home_team_name"),
            away_team_name=config.get("away_team_name"),
            game_date=config.get("game_date"),
            search_queries=config.get("search_queries"),
            sport_type=context.sport_type,
        )
