"""World Cup pre-game betting DataStream.

Thin subclass of ``NBAPreGameBettingDataHubDataStream`` that extends the
sport→ESPN league mapping to include soccer/FIFA codes. All pregame web
search, social media, and stats-fetch behavior is inherited unchanged.
"""

from __future__ import annotations

import logging

from dojozero.core import RuntimeContext
from dojozero.data import DataHub
from dojozero.data._context import GameContext
from dojozero.data.espn._api import ESPNExternalAPI
from dojozero.data.websearch._api import WebSearchAPI
from dojozero.nba._datastream import (
    NBAPreGameBettingDataHubDataStream,
    NBAPreGameBettingDataHubDataStreamConfig,
)

logger = logging.getLogger(__name__)


class WorldCupPreGameBettingDataHubDataStreamConfig(
    NBAPreGameBettingDataHubDataStreamConfig, total=False
):
    """Configuration for World Cup pre-game DataHubDataStream.

    Adds a single optional field: ``league`` (FIFA league code, e.g.
    ``fifa.world``). All other keys are inherited from the NBA config.
    """

    league: str


class WorldCupPreGameBettingDataHubDataStream(NBAPreGameBettingDataHubDataStream):
    """World Cup pre-game DataStream.

    Behavior is identical to the NBA stream; only the sport→(ESPN sport, league)
    mapping is extended for soccer so the optional ESPN stats fetch (when a
    soccer pregame_stats fetcher is added later) uses the right path.
    """

    @classmethod
    def from_dict(
        cls,
        config: WorldCupPreGameBettingDataHubDataStreamConfig,
        context: RuntimeContext,
    ) -> "WorldCupPreGameBettingDataHubDataStream":
        hub: DataHub | None = None
        hub_id = config.get("hub_id", "default_hub")
        hub = context.data_hubs.get(hub_id)
        if hub is None:
            persistence_file = config.get("persistence_file", "outputs/events.jsonl")
            hub = DataHub(hub_id=hub_id, persistence_file=persistence_file)

        search_api: WebSearchAPI | None = None
        game_context: GameContext | None = None
        espn_api: "ESPNExternalAPI | None" = None

        ws_event_types = config.get("websearch_event_types", [])
        stats_event_types = config.get("stats_event_types", [])
        sm_event_types = config.get("socialmedia_event_types", [])

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
            league_override = config.get("league")
            league = league_override if league_override else "fifa.world"
            espn_api = ESPNExternalAPI(sport="soccer", league=league)

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


__all__ = [
    "WorldCupPreGameBettingDataHubDataStream",
    "WorldCupPreGameBettingDataHubDataStreamConfig",
]
