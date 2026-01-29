"""NFL pre-game betting DataStream with web search event class lifecycle."""

import asyncio
import logging
from typing import TypedDict

from dojozero.core import RuntimeContext
from dojozero.data import DataHub
from dojozero.data._models import DataEvent
from dojozero.data._streams import DataHubDataStream as BaseDataHubDataStream
from dojozero.data.websearch._api import WebSearchAPI
from dojozero.data.websearch._context import GameContext
from dojozero.data.websearch._events import WebSearchEventMixin

logger = logging.getLogger(__name__)


def _resolve_websearch_classes(
    suffixes: set[str],
) -> list[type[WebSearchEventMixin]]:
    """Resolve event type suffixes to WebSearchEventMixin subclasses.

    Discovers classes via ``WebSearchEventMixin.__subclasses__()`` and matches
    their ``event_type`` default (e.g. ``"event.injury_report"``) against the
    provided suffixes (e.g. ``{"injury_report"}``).
    """
    matched: list[type[WebSearchEventMixin]] = []
    for cls in WebSearchEventMixin.__subclasses__():
        event_type = cls.model_fields["event_type"].default  # type: ignore[attr-defined]
        if (
            isinstance(event_type, str)
            and event_type.removeprefix("event.") in suffixes
        ):
            matched.append(cls)
    return matched


class _ActorIdConfig(TypedDict):
    actor_id: str


class NFLPreGameBettingDataHubDataStreamConfig(_ActorIdConfig, total=False):
    """Configuration for NFL pre-game betting DataHubDataStream."""

    hub_id: str
    persistence_file: str
    event_type: str  # Which event_type to subscribe to in DataHub
    event_types: list[
        str
    ]  # Which event_types to subscribe to (alternative to event_type)
    home_team_abbreviation: str  # Team metadata for generating queries
    away_team_abbreviation: str
    home_team_name: str  # Full team name for search queries
    away_team_name: str
    game_date: str
    game_id: str  # ESPN game ID for populating event.game_id
    websearch_event_types: list[
        str
    ]  # Canonical suffixes that need web search (e.g., ["injury_report"])


class NFLPreGameBettingDataHubDataStream(BaseDataHubDataStream):
    """NFL DataStream that extends generic DataHubDataStream.

    Adds NFL-specific initialization logic: for each configured web search
    event class, calls ``EventClass.from_web_search()`` to run the full
    search → LLM → typed event lifecycle, then publishes to DataHub.
    """

    def __init__(
        self,
        *,
        actor_id: str,
        trial_id: str,
        hub: DataHub | None = None,
        event_type: str | None = None,
        event_types: list[str] | None = None,
        search_api: WebSearchAPI | None = None,
        game_context: GameContext | None = None,
        websearch_event_types: list[str] | None = None,
        sport_type: str = "",
    ) -> None:
        super().__init__(
            actor_id=actor_id,
            trial_id=trial_id,
            hub=hub,
            event_type=event_type,
            event_types=event_types,
            sport_type=sport_type,
        )
        self._search_api = search_api
        self._game_context = game_context
        self._websearch_event_types = set(websearch_event_types or [])
        self._search_initialized = False

    async def start(self) -> None:
        """Subscribe to DataHub events and trigger web searches in background."""
        # Call parent start() which handles DataHub subscription
        await super().start()

        # Run web searches and await completion so all pre-game insights
        # are emitted to the hub before stores start polling.  The hub's
        # lifecycle gate holds back GameStartEvent / play events until
        # PREGAME insights have been delivered.
        if (
            self._websearch_event_types
            and self._search_api
            and self._game_context
            and not self._search_initialized
        ):
            self._search_initialized = True
            await self._run_web_searches()

    async def _run_web_searches(self) -> None:
        """Trigger web searches for all configured event classes in parallel.

        Discovers event classes via ``WebSearchEventMixin.__subclasses__()``
        and matches against the configured event type suffixes.  All searches
        run concurrently via ``asyncio.gather``; results are published to
        the hub sequentially after all searches complete.
        """
        assert self._search_api is not None
        assert self._game_context is not None
        search_api = self._search_api
        game_context = self._game_context

        # Resolve event classes from mixin subclass tree
        event_classes = _resolve_websearch_classes(self._websearch_event_types)
        logger.info(
            "stream '%s' triggering %d web searches in parallel",
            self.actor_id,
            len(event_classes),
        )

        async def _fetch_one(
            event_cls: type[WebSearchEventMixin],
        ) -> DataEvent | None:
            try:
                logger.info(
                    "stream '%s' running %s.from_web_search()",
                    self.actor_id,
                    event_cls.__name__,
                )
                event = await event_cls.from_web_search(
                    api=search_api,
                    context=game_context,
                )
                if event and isinstance(event, DataEvent):
                    return event
            except Exception as e:
                logger.error(
                    "stream '%s' failed to fetch %s: %s",
                    self.actor_id,
                    event_cls.__name__,
                    e,
                    exc_info=True,
                )
            return None

        # Run all searches concurrently
        results = await asyncio.gather(*[_fetch_one(cls) for cls in event_classes])

        # Publish results sequentially to the hub
        for event_cls, event in zip(event_classes, results):
            if event and self._hub:
                await self._hub.receive_event(event)
                logger.info(
                    "stream '%s' published %s event",
                    self.actor_id,
                    event_cls.__name__,
                )

    @classmethod
    def from_dict(
        cls,
        config: NFLPreGameBettingDataHubDataStreamConfig,
        context: RuntimeContext,
    ) -> "NFLPreGameBettingDataHubDataStream":
        # Get hub from context
        hub: DataHub | None = None
        hub_id = config.get("hub_id", "default_hub")
        hub = context.data_hubs.get(hub_id)

        if hub is None:
            persistence_file = config.get("persistence_file", "outputs/events.jsonl")
            hub = DataHub(hub_id=hub_id, persistence_file=persistence_file)

        # Build search API and game context if websearch event types configured
        search_api: WebSearchAPI | None = None
        game_context: GameContext | None = None

        ws_event_types = config.get("websearch_event_types", [])
        if ws_event_types:
            search_api = WebSearchAPI()
            game_context = GameContext(
                sport=context.sport_type,
                home_team=config.get("home_team_name", ""),
                away_team=config.get("away_team_name", ""),
                home_tricode=config.get("home_team_abbreviation", ""),
                away_tricode=config.get("away_team_abbreviation", ""),
                game_date=config.get("game_date", ""),
                game_id=config.get("game_id", ""),
            )

        return cls(
            actor_id=config["actor_id"],
            trial_id=context.trial_id,
            hub=hub,
            event_type=config.get("event_type"),
            event_types=config.get("event_types", []),
            search_api=search_api,
            game_context=game_context,
            websearch_event_types=ws_event_types,
            sport_type=context.sport_type,
        )
