"""World Cup ExternalAPI implementation using ESPN API.

Wraps the generic ESPNExternalAPI with sport="soccer" and a configurable
league code so a single API class serves all FIFA competitions:

    fifa.world              — FIFA World Cup (men's)
    fifa.wwc                — FIFA Women's World Cup
    fifa.cwc                — FIFA Club World Cup
    fifa.worldq.{uefa,concacaf,conmebol,afc,caf,ofc}  — qualifying confederations
"""

import logging
from typing import Any

from dojozero.data._stores import ExternalAPI
from dojozero.data.espn import ESPNExternalAPI

logger = logging.getLogger(__name__)


DEFAULT_LEAGUE = "fifa.world"


class WorldCupExternalAPI(ExternalAPI):
    """ESPN soccer API implementation parameterised by FIFA league code.

    Endpoints:
    - scoreboard: Get all matches for a date (params: dates=YYYYMMDD)
    - summary:    Get full match data by event_id (params: event_id)
    - plays:      Get play-by-play data by event_id (params: event_id)
    - teams:      Get all teams for the league

    Legacy endpoint aliases mirror NBAExternalAPI so the store can speak the
    same endpoint vocabulary:
    - boxscore     -> summary
    - play_by_play -> plays

    Proxy support: set DOJOZERO_PROXY_URL or pass ``proxy=``.
    """

    def __init__(
        self,
        league: str = DEFAULT_LEAGUE,
        timeout: int = 30,
        proxy: str | None = None,
    ):
        """Initialize World Cup API.

        Args:
            league: FIFA league code (default "fifa.world")
            timeout: Request timeout in seconds
            proxy: Optional proxy URL. If None, ESPNExternalAPI reads DOJOZERO_PROXY_URL.
        """
        super().__init__()
        self.league = league
        self._api = ESPNExternalAPI(
            sport="soccer",
            league=league,
            timeout=timeout,
            proxy=proxy,
        )

    async def fetch(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch World Cup data from ESPN soccer API.

        Args:
            endpoint: One of "scoreboard", "summary", "plays", "teams",
                or legacy aliases "boxscore" (→summary), "play_by_play" (→plays).
            params: Endpoint-specific parameters.

        Returns:
            Raw ESPN API response as dict.
        """
        params = params or {}

        if endpoint == "boxscore":
            event_id = params.get("game_id") or params.get("event_id")
            if not event_id:
                return {"summary": {"eventId": ""}}
            return await self._api.fetch("summary", {"event_id": event_id})

        if endpoint == "play_by_play":
            event_id = params.get("game_id") or params.get("event_id")
            if not event_id:
                return {"plays": {"items": [], "eventId": ""}}
            return await self._api.fetch("plays", {"event_id": event_id})

        return await self._api.fetch(endpoint, params)

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        await self._api.close()


__all__ = ["WorldCupExternalAPI", "DEFAULT_LEAGUE"]
