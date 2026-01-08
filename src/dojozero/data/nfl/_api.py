"""NFL ExternalAPI implementation using ESPN API."""

import logging
from typing import Any

import aiohttp

from dojozero.data._stores import ExternalAPI
from dojozero.data.nfl._utils import get_proxy

logger = logging.getLogger(__name__)

# ESPN API base URLs
SITE_API_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE_API_BASE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"


class NFLExternalAPI(ExternalAPI):
    """ESPN NFL API implementation.

    Endpoints:
    - scoreboard: Get all games for a date/week
    - summary: Get full game data by event_id
    - plays: Get play-by-play data by event_id
    - teams: Get all NFL teams

    Proxy support:
    - Set DOJOZERO_PROXY_URL environment variable to use a proxy
    - Example: export DOJOZERO_PROXY_URL="http://proxy.example.com:8080"
    """

    def __init__(self, timeout: int = 30, proxy: str | None = None):
        """Initialize NFL API.

        Args:
            timeout: Request timeout in seconds
            proxy: Optional proxy URL. If not provided, will use DOJOZERO_PROXY_URL env var
        """
        super().__init__()
        self.timeout = timeout
        self._proxy = proxy if proxy is not None else get_proxy()
        self._session: aiohttp.ClientSession | None = None

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
        """Fetch NFL data from ESPN API.

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
            week: Week number (optional)
            seasontype: 1=preseason, 2=regular, 3=postseason (optional)

        Returns:
            {"scoreboard": {...}} with events, leagues, calendar
        """
        url = f"{SITE_API_BASE}/scoreboard"
        query_params: dict[str, str] = {}

        if "dates" in params:
            query_params["dates"] = str(params["dates"])
        if "week" in params:
            query_params["week"] = str(params["week"])
        if "seasontype" in params:
            query_params["seasontype"] = str(params["seasontype"])

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
            {"summary": {...}} with boxscore, drives, header, pickcenter, etc.
        """
        event_id = params.get("event_id")
        if not event_id:
            logger.warning("summary endpoint requires event_id param")
            return {"summary": {}}

        url = f"{SITE_API_BASE}/summary"
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
                # Add event_id to response for easier tracking
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
        url = f"{CORE_API_BASE}/events/{event_id}/competitions/{event_id}/plays"
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
                # Add event_id to response for easier tracking
                data["eventId"] = event_id
                return {"plays": data}
        except Exception as e:
            logger.error("Error fetching plays for event %s: %s", event_id, e)
            return {"plays": {"items": [], "eventId": event_id}}

    async def _fetch_teams(self) -> dict[str, Any]:
        """Fetch all NFL teams.

        Returns:
            {"teams": [...]} with team info
        """
        url = f"{SITE_API_BASE}/teams"

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
