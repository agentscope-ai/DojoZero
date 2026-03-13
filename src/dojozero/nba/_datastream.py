"""NBA pre-game betting DataStream with web search event class lifecycle."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, TypedDict

from dojozero.core import RuntimeContext
from dojozero.data import DataHub
from dojozero.data._models import DataEvent
from dojozero.data._streams import DataHubDataStream as BaseDataHubDataStream
from dojozero.data.websearch._api import WebSearchAPI
from dojozero.data._context import GameContext
from dojozero.data.websearch._events import WebSearchEventMixin
from dojozero.data.socialmedia._events import SocialMediaEventMixin

if TYPE_CHECKING:
    from dojozero.data.espn._api import ESPNExternalAPI

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


def _resolve_socialmedia_classes(
    suffixes: set[str],
) -> list[type[SocialMediaEventMixin]]:
    """Resolve event type suffixes to SocialMediaEventMixin subclasses.

    Discovers classes via ``SocialMediaEventMixin.__subclasses__()`` and matches
    their ``event_type`` default (e.g. ``"event.twitter_top_tweets"``) against the
    provided suffixes (e.g. ``{"twitter_top_tweets"}``).
    """
    matched: list[type[SocialMediaEventMixin]] = []
    for cls in SocialMediaEventMixin.__subclasses__():
        event_type = cls.model_fields["event_type"].default  # type: ignore[attr-defined]
        if (
            isinstance(event_type, str)
            and event_type.removeprefix("event.") in suffixes
        ):
            matched.append(cls)
    return matched


class _ActorIdConfig(TypedDict):
    actor_id: str


class NBAPreGameBettingDataHubDataStreamConfig(_ActorIdConfig, total=False):
    """Configuration for NBA pre-game betting DataHubDataStream."""

    hub_id: str
    persistence_file: str
    event_type: str  # Which event_type to subscribe to in DataHub
    event_types: list[
        str
    ]  # Which event_types to subscribe to (alternative to event_type)
    home_team_tricode: str  # Team metadata for generating queries
    away_team_tricode: str
    home_team_name: str  # Full team name for search queries
    away_team_name: str
    game_date: str
    game_id: str  # ESPN game ID for populating event.game_id
    websearch_event_types: list[
        str
    ]  # Canonical suffixes that need web search (e.g., ["injury_report"])
    stats_event_types: list[
        str
    ]  # Canonical suffixes that need ESPN stats (e.g., ["pregame_stats"])
    socialmedia_event_types: list[
        str
    ]  # Social media event types (e.g., ["twitter_top_tweets"])
    home_team_id: str  # ESPN team ID for home team
    away_team_id: str  # ESPN team ID for away team
    season_year: int
    season_type: str
    venue_timezone: str  # IANA timezone for venue (e.g., "America/New_York")


class NBAPreGameBettingDataHubDataStream(BaseDataHubDataStream):
    """NBA pre-game betting DataStream that extends generic DataHubDataStream.

    Adds NBA-specific initialization logic: for each configured web search
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
        stats_event_types: list[str] | None = None,
        socialmedia_event_types: list[str] | None = None,
        espn_api: "ESPNExternalAPI | None" = None,
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
        self._stats_event_types = set(stats_event_types or [])
        self._socialmedia_event_types = set(socialmedia_event_types or [])
        self._espn_api = espn_api
        self._search_initialized = False

    async def start(self) -> None:
        """Subscribe to DataHub events and register pregame callback."""
        # Call parent start() which handles DataHub subscription
        await super().start()

        # Register a callback on the hub so that when GameInitializeEvent
        # fires, stores are paused and pregame data fetching runs before
        # polling resumes.
        has_websearch = (
            self._websearch_event_types and self._search_api and self._game_context
        )
        has_stats = self._stats_event_types and self._espn_api and self._game_context
        has_socialmedia = self._socialmedia_event_types and self._game_context
        if (has_websearch or has_stats or has_socialmedia) and self._hub:
            self._hub.add_on_game_initialized(self._on_game_initialized)

    async def _on_game_initialized(self, _game_id: str) -> None:
        """Hub callback: run pre-game data fetching while stores are paused."""
        if not self._search_initialized:
            self._search_initialized = True
            # Run web searches, stats fetch, and social media collection concurrently
            tasks: list[asyncio.Task[None]] = []
            if self._websearch_event_types and self._search_api:
                tasks.append(asyncio.create_task(self._run_web_searches()))
            if self._stats_event_types and self._espn_api and self._game_context:
                tasks.append(asyncio.create_task(self._run_stats_fetch()))
            if self._socialmedia_event_types and self._game_context:
                tasks.append(asyncio.create_task(self._run_social_media_collection()))
            if tasks:
                await asyncio.gather(*tasks)

    # Timeout (seconds) for each individual web search (search + LLM).
    _WEB_SEARCH_TIMEOUT: float = 120.0

    async def _run_web_searches(self) -> None:
        """Trigger web searches for all configured event classes in parallel.

        Discovers event classes via ``WebSearchEventMixin.__subclasses__()``
        and matches against the configured event type suffixes.  All searches
        run concurrently via ``asyncio.gather``; results are published to
        the hub sequentially after all searches complete.

        Each individual search is guarded by ``_WEB_SEARCH_TIMEOUT`` so that
        a hanging search never blocks the pipeline.
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
                event = await asyncio.wait_for(
                    event_cls.from_web_search(
                        api=search_api,
                        context=game_context,
                    ),
                    timeout=self._WEB_SEARCH_TIMEOUT,
                )
                if event and isinstance(event, DataEvent):
                    return event
            except asyncio.TimeoutError:
                logger.warning(
                    "stream '%s' timed out fetching %s after %.0fs",
                    self.actor_id,
                    event_cls.__name__,
                    self._WEB_SEARCH_TIMEOUT,
                )
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
        succeeded = 0
        for event_cls, event in zip(event_classes, results):
            if event and self._hub:
                await self._hub.receive_event(event)
                succeeded += 1
                logger.info(
                    "stream '%s' published %s event",
                    self.actor_id,
                    event_cls.__name__,
                )

        logger.info(
            "stream '%s' web searches complete: %d/%d succeeded",
            self.actor_id,
            succeeded,
            len(event_classes),
        )

    _STATS_FETCH_TIMEOUT: float = 60.0
    _SOCIAL_MEDIA_TIMEOUT: float = 120.0

    async def _run_social_media_collection(self) -> None:
        """Trigger social media collection for all configured event classes in parallel.

        Uses X API to fetch tweets from curated watchlist accounts.
        """
        assert self._game_context is not None
        game_context = self._game_context

        # Create SocialMediaAPI instance
        from dojozero.data.socialmedia._api import SocialMediaAPI

        try:
            social_api = SocialMediaAPI()
        except (ImportError, ValueError) as e:
            logger.error("Failed to create SocialMediaAPI: %s", e)
            return

        # Resolve event classes from mixin subclass tree
        event_classes = _resolve_socialmedia_classes(self._socialmedia_event_types)
        logger.info(
            "stream '%s' triggering %d social media collections in parallel",
            self.actor_id,
            len(event_classes),
        )

        async def _fetch_one(
            event_cls: type[SocialMediaEventMixin],
        ) -> DataEvent | None:
            try:
                logger.info(
                    "stream '%s' running %s.from_social_media()",
                    self.actor_id,
                    event_cls.__name__,
                )
                event = await asyncio.wait_for(
                    event_cls.from_social_media(
                        api=social_api,
                        context=game_context,
                    ),
                    timeout=self._SOCIAL_MEDIA_TIMEOUT,
                )
                if event and isinstance(event, DataEvent):
                    return event
            except asyncio.TimeoutError:
                logger.warning(
                    "stream '%s' timed out fetching %s after %.0fs",
                    self.actor_id,
                    event_cls.__name__,
                    self._SOCIAL_MEDIA_TIMEOUT,
                )
            except Exception as e:
                logger.error(
                    "stream '%s' failed to fetch %s: %s",
                    self.actor_id,
                    event_cls.__name__,
                    e,
                    exc_info=True,
                )
            return None

        # Run all collections concurrently
        results = await asyncio.gather(*[_fetch_one(cls) for cls in event_classes])

        # Publish results sequentially to the hub
        succeeded = 0
        for event_cls, event in zip(event_classes, results):
            if event and self._hub:
                await self._hub.receive_event(event)
                succeeded += 1
                logger.info(
                    "stream '%s' published %s event",
                    self.actor_id,
                    event_cls.__name__,
                )

        logger.info(
            "stream '%s' social media collections complete: %d/%d succeeded",
            self.actor_id,
            succeeded,
            len(event_classes),
        )

    async def _run_stats_fetch(self) -> None:
        """Fetch pre-game stats from ESPN API and publish to hub."""
        from dojozero.data.espn._stats_fetcher import fetch_pregame_stats

        assert self._espn_api is not None
        assert self._game_context is not None
        ctx = self._game_context

        logger.info(
            "stream '%s' fetching pregame stats from ESPN",
            self.actor_id,
        )

        try:
            event = await asyncio.wait_for(
                fetch_pregame_stats(
                    self._espn_api,
                    home_team_id=ctx.home_team_id,
                    away_team_id=ctx.away_team_id,
                    game_id=ctx.game_id,
                    game_date=ctx.game_date,
                    sport=ctx.sport,
                    season_year=ctx.season_year,
                    season_type=ctx.season_type,
                    home_team_name=ctx.home_team,
                    away_team_name=ctx.away_team,
                    venue_timezone=ctx.venue_timezone,
                ),
                timeout=self._STATS_FETCH_TIMEOUT,
            )
            if event and self._hub:
                await self._hub.receive_event(event)
                logger.info(
                    "stream '%s' published PreGameStatsEvent",
                    self.actor_id,
                )
        except asyncio.TimeoutError:
            logger.warning(
                "stream '%s' timed out fetching pregame stats after %.0fs",
                self.actor_id,
                self._STATS_FETCH_TIMEOUT,
            )
        except Exception as e:
            logger.error(
                "stream '%s' failed to fetch pregame stats: %s",
                self.actor_id,
                e,
                exc_info=True,
            )

    @classmethod
    def from_dict(
        cls,
        config: NBAPreGameBettingDataHubDataStreamConfig,
        context: RuntimeContext,
    ) -> "NBAPreGameBettingDataHubDataStream":
        # Get hub from context
        hub: DataHub | None = None
        hub_id = config.get("hub_id", "default_hub")
        hub = context.data_hubs.get(hub_id)

        if hub is None:
            persistence_file = config.get("persistence_file", "outputs/events.jsonl")
            hub = DataHub(hub_id=hub_id, persistence_file=persistence_file)

        # Build search API and game context
        search_api: WebSearchAPI | None = None
        game_context: GameContext | None = None
        espn_api: ESPNExternalAPI | None = None

        ws_event_types = config.get("websearch_event_types", [])
        stats_event_types = config.get("stats_event_types", [])
        sm_event_types = config.get("socialmedia_event_types", [])

        # Build GameContext if any pregame data fetching is needed
        if ws_event_types or stats_event_types or sm_event_types:
            game_context = GameContext(
                sport=context.sport_type,
                home_team=config.get("home_team_name", ""),
                away_team=config.get("away_team_name", ""),
                home_tricode=config.get("home_team_tricode", ""),
                away_tricode=config.get("away_team_tricode", ""),
                game_date=config.get("game_date", ""),
                game_id=config.get("game_id", ""),
                home_team_id=config.get("home_team_id", ""),
                away_team_id=config.get("away_team_id", ""),
                season_year=config.get("season_year", 0),
                season_type=config.get("season_type", ""),
                venue_timezone=config.get("venue_timezone", ""),
            )

        if ws_event_types or sm_event_types:
            search_api = WebSearchAPI()

        if stats_event_types:
            from dojozero.data.espn._api import ESPNExternalAPI as _ESPNExternalAPI

            # Map sport type to ESPN sport/league
            sport_map = {
                "nba": ("basketball", "nba"),
                "nfl": ("football", "nfl"),
            }
            sport, league = sport_map.get(context.sport_type, ("basketball", "nba"))
            espn_api = _ESPNExternalAPI(sport=sport, league=league)

        return cls(
            actor_id=config["actor_id"],
            trial_id=context.trial_id,
            hub=hub,
            event_type=config.get("event_type"),
            event_types=config.get("event_types", []),
            search_api=search_api,
            game_context=game_context,
            websearch_event_types=ws_event_types,
            stats_event_types=stats_event_types,
            socialmedia_event_types=sm_event_types,
            espn_api=espn_api,
            sport_type=context.sport_type,
        )
