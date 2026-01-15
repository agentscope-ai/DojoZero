"""ESPN ExternalAPI base implementation.

Provides a generic ESPN API client that works with any sport/league combination.
The ESPN API is unofficial/undocumented - no published rate limits, but community
recommends reasonable polling intervals (10-60s) and local caching.

Supported sports and leagues:
    - football/nfl, football/college-football
    - basketball/nba, basketball/mens-college-basketball, basketball/wnba
    - baseball/mlb
    - hockey/nhl
    - soccer/eng.1, soccer/usa.1, soccer/esp.1, etc.
    - tennis/atp, tennis/wta
    - golf/pga
    - mma/ufc
    - racing/f1
"""

import logging
import os
from typing import Any

import aiohttp

from dojozero.data._stores import ExternalAPI

logger = logging.getLogger(__name__)

# ESPN API base URLs
SITE_API_BASE = "https://site.api.espn.com/apis/site/v2/sports"
CORE_API_BASE = "https://sports.core.api.espn.com/v2/sports"


def get_proxy() -> str | None:
    """Get proxy configuration from environment variables.

    Returns:
        Proxy URL string, or None if not configured
    """
    return os.getenv("DOJOZERO_PROXY_URL")


class ESPNExternalAPI(ExternalAPI):
    """Generic ESPN API implementation for any sport/league.

    Endpoints:
    - scoreboard: Get all games for a date
    - summary: Get full game data by event_id
    - plays: Get play-by-play data by event_id
    - teams: Get all teams for the league

    Example:
        # NFL
        api = ESPNExternalAPI(sport="football", league="nfl")

        # NCAA Basketball
        api = ESPNExternalAPI(sport="basketball", league="mens-college-basketball")

        # English Premier League
        api = ESPNExternalAPI(sport="soccer", league="eng.1")
    """

    def __init__(
        self,
        sport: str,
        league: str,
        timeout: int = 30,
        proxy: str | None = None,
    ):
        """Initialize ESPN API.

        Args:
            sport: Sport type (e.g., "football", "basketball", "soccer")
            league: League identifier (e.g., "nfl", "nba", "eng.1")
            timeout: Request timeout in seconds
            proxy: Optional proxy URL. If not provided, will use DOJOZERO_PROXY_URL env var
        """
        super().__init__()
        self.sport = sport
        self.league = league
        self.timeout = timeout
        self._proxy = proxy if proxy is not None else get_proxy()
        self._session: aiohttp.ClientSession | None = None

    @property
    def site_api_url(self) -> str:
        """Get the site API base URL for this sport/league."""
        return f"{SITE_API_BASE}/{self.sport}/{self.league}"

    @property
    def core_api_url(self) -> str:
        """Get the core API base URL for this sport/league."""
        return f"{CORE_API_BASE}/{self.sport}/leagues/{self.league}"

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch data from ESPN API.

        Args:
            endpoint: API endpoint ("scoreboard", "summary", "plays", "teams")
            params: Request parameters (varies by endpoint)

        Returns:
            API response as dict
        """
        params = params or {}

        if endpoint == "scoreboard":
            return await self._fetch_scoreboard(params)
        elif endpoint == "summary":
            return await self._fetch_summary(params)
        elif endpoint == "plays":
            return await self._fetch_plays(params)
        elif endpoint == "teams":
            return await self._fetch_teams()
        else:
            logger.warning("Unknown endpoint: %s", endpoint)
            return {}

    async def _fetch_scoreboard(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch scoreboard data.

        Params:
            dates: Date string YYYYMMDD (optional)
            week: Week number - for NFL/college football (optional)
            seasontype: 1=preseason, 2=regular, 3=postseason (optional)
            limit: Max events to return (optional)

        Returns:
            {"scoreboard": {...}} with events, leagues, calendar
        """
        url = f"{self.site_api_url}/scoreboard"
        query_params: dict[str, str] = {}

        if "dates" in params:
            query_params["dates"] = str(params["dates"])
        if "week" in params:
            query_params["week"] = str(params["week"])
        if "seasontype" in params:
            query_params["seasontype"] = str(params["seasontype"])
        if "limit" in params:
            query_params["limit"] = str(params["limit"])

        session = await self._get_session()
        try:
            async with session.get(
                url, params=query_params, proxy=self._proxy
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "Scoreboard request failed: status=%d, url=%s",
                        response.status,
                        url,
                    )
                    return {"scoreboard": {"events": []}}

                data = await response.json()
                return {"scoreboard": data}
        except Exception as e:
            logger.error("Error fetching scoreboard: %s", e)
            return {"scoreboard": {"events": []}}

    async def _fetch_summary(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch game summary data.

        Params:
            event_id: ESPN event ID (required)

        Returns:
            {"summary": {...}} with boxscore, drives/plays, header, etc.
        """
        event_id = params.get("event_id")
        if not event_id:
            logger.warning("summary endpoint requires event_id param")
            return {"summary": {}}

        url = f"{self.site_api_url}/summary"
        query_params = {"event": str(event_id)}

        session = await self._get_session()
        try:
            async with session.get(
                url, params=query_params, proxy=self._proxy
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "Summary request failed: status=%d, event_id=%s",
                        response.status,
                        event_id,
                    )
                    return {"summary": {"eventId": event_id}}

                data = await response.json()
                data["eventId"] = event_id
                return {"summary": data}
        except Exception as e:
            logger.error("Error fetching summary for event %s: %s", event_id, e)
            return {"summary": {"eventId": event_id}}

    async def _fetch_plays(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch play-by-play data.

        Params:
            event_id: ESPN event ID (required)
            limit: Max number of plays (default 300)

        Returns:
            {"plays": {...}} with items array of plays
        """
        event_id = params.get("event_id")
        if not event_id:
            logger.warning("plays endpoint requires event_id param")
            return {"plays": {"items": [], "eventId": ""}}

        limit = params.get("limit", 300)
        url = f"{self.core_api_url}/events/{event_id}/competitions/{event_id}/plays"
        query_params = {"limit": str(limit)}

        session = await self._get_session()
        try:
            async with session.get(
                url, params=query_params, proxy=self._proxy
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "Plays request failed: status=%d, event_id=%s",
                        response.status,
                        event_id,
                    )
                    return {"plays": {"items": [], "eventId": event_id}}

                data = await response.json()
                data["eventId"] = event_id
                return {"plays": data}
        except Exception as e:
            logger.error("Error fetching plays for event %s: %s", event_id, e)
            return {"plays": {"items": [], "eventId": event_id}}

    async def _fetch_teams(self) -> dict[str, Any]:
        """Fetch all teams for the league.

        Returns:
            {"teams": [...]} with team info
        """
        url = f"{self.site_api_url}/teams"

        session = await self._get_session()
        try:
            async with session.get(url, proxy=self._proxy) as response:
                if response.status != 200:
                    logger.warning("Teams request failed: status=%d", response.status)
                    return {"teams": []}

                data = await response.json()
                # Extract teams from nested structure
                teams = []
                for sport in data.get("sports", []):
                    for league in sport.get("leagues", []):
                        for team_wrapper in league.get("teams", []):
                            team = team_wrapper.get("team", {})
                            if team:
                                teams.append(team)
                return {"teams": teams}
        except Exception as e:
            logger.error("Error fetching teams: %s", e)
            return {"teams": []}
