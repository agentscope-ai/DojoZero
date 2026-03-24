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
SITE_V2_API_BASE = "https://site.api.espn.com/apis/v2/sports"
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

    @property
    def site_v2_api_url(self) -> str:
        """Get the site v2 API base URL for this sport/league.

        Used by endpoints like standings that live under ``/apis/v2/``
        rather than ``/apis/site/v2/``.
        """
        return f"{SITE_V2_API_BASE}/{self.sport}/{self.league}"

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
        elif endpoint == "team_schedule":
            return await self._fetch_team_schedule(params)
        elif endpoint == "team_statistics":
            return await self._fetch_team_statistics(params)
        elif endpoint == "standings":
            return await self._fetch_standings(params)
        elif endpoint == "team_roster":
            return await self._fetch_team_roster(params)
        elif endpoint == "team_leaders":
            return await self._fetch_team_leaders(params)
        elif endpoint == "game_roster":
            return await self._fetch_game_roster(params)
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
        """Fetch play-by-play data with automatic pagination.

        The ESPN core API returns at most ``limit`` plays per page.  This
        method fetches all pages so that a full game (~500+ plays) is
        returned in a single call.

        Params:
            event_id: ESPN event ID (required)
            limit: Per-page limit (default 300)

        Returns:
            {"plays": {...}} with items array of **all** plays
        """
        event_id = params.get("event_id")
        if not event_id:
            logger.warning("plays endpoint requires event_id param")
            return {"plays": {"items": [], "eventId": ""}}

        limit = params.get("limit", 300)
        url = f"{self.core_api_url}/events/{event_id}/competitions/{event_id}/plays"

        all_items: list[dict[str, Any]] = []
        page = 1
        total_count = 0

        session = await self._get_session()
        try:
            while True:
                query_params = {"limit": str(limit), "page": str(page)}
                async with session.get(
                    url, params=query_params, proxy=self._proxy
                ) as response:
                    if response.status != 200:
                        logger.warning(
                            "Plays request failed: status=%d, event_id=%s, page=%d",
                            response.status,
                            event_id,
                            page,
                        )
                        break

                    data = await response.json()
                    items = data.get("items", [])
                    all_items.extend(items)
                    total_count = data.get("count", len(all_items))
                    page_count = data.get("pageCount", 1)

                    if page >= page_count:
                        break
                    page += 1

        except Exception as e:
            logger.error("Error fetching plays for event %s: %s", event_id, e)

        return {
            "plays": {
                "items": all_items,
                "count": total_count,
                "eventId": event_id,
            }
        }

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

    async def _fetch_team_schedule(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch team schedule data.

        Params:
            team_id: ESPN team ID (required)
            season: Season year (optional, e.g., 2025)

        Returns:
            {"team_schedule": {...}} with events array
        """
        team_id = params.get("team_id")
        if not team_id:
            logger.warning("team_schedule endpoint requires team_id param")
            return {"team_schedule": {"events": []}}

        url = f"{self.site_api_url}/teams/{team_id}/schedule"
        query_params: dict[str, str] = {}
        if "season" in params:
            query_params["season"] = str(params["season"])

        session = await self._get_session()
        try:
            async with session.get(
                url, params=query_params, proxy=self._proxy
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "Team schedule request failed: status=%d, team_id=%s",
                        response.status,
                        team_id,
                    )
                    return {"team_schedule": {"events": []}}

                data = await response.json()
                return {"team_schedule": data}
        except Exception as e:
            logger.error("Error fetching team schedule for %s: %s", team_id, e)
            return {"team_schedule": {"events": []}}

    async def _fetch_team_statistics(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch team season statistics.

        Params:
            team_id: ESPN team ID (required)
            season_year: Season year (required, e.g., 2025)
            season_type: Season type string (optional, default "regular")
                         Maps: "preseason"->1, "regular"->2, "postseason"->3

        Returns:
            {"team_statistics": {...}} with splits/categories
        """
        team_id = params.get("team_id")
        if not team_id:
            logger.warning("team_statistics endpoint requires team_id param")
            return {"team_statistics": {}}

        season_year = params.get("season_year")
        if not season_year:
            logger.warning("team_statistics endpoint requires season_year param")
            return {"team_statistics": {}}

        season_type_str = params.get("season_type", "regular")
        season_type_map = {"preseason": 1, "regular": 2, "postseason": 3}
        season_type_id = season_type_map.get(season_type_str, 2)

        url = (
            f"{self.core_api_url}/seasons/{season_year}"
            f"/types/{season_type_id}/teams/{team_id}/statistics"
        )

        session = await self._get_session()
        try:
            async with session.get(url, proxy=self._proxy) as response:
                if response.status != 200:
                    logger.warning(
                        "Team statistics request failed: status=%d, team_id=%s",
                        response.status,
                        team_id,
                    )
                    return {"team_statistics": {}}

                data = await response.json()
                return {"team_statistics": data}
        except Exception as e:
            logger.error("Error fetching team statistics for %s: %s", team_id, e)
            return {"team_statistics": {}}

    async def _fetch_standings(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch league standings.

        Params:
            season: Season year (optional)
            group: Group filter (optional, e.g., conference)

        Returns:
            {"standings": {...}} with children array of conference/division standings
        """
        url = f"{self.site_v2_api_url}/standings"
        query_params: dict[str, str] = {}
        if "season" in params:
            query_params["season"] = str(params["season"])
        if "group" in params:
            query_params["group"] = str(params["group"])

        session = await self._get_session()
        try:
            async with session.get(
                url, params=query_params, proxy=self._proxy
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "Standings request failed: status=%d", response.status
                    )
                    return {"standings": {"children": []}}

                data = await response.json()
                return {"standings": data}
        except Exception as e:
            logger.error("Error fetching standings: %s", e)
            return {"standings": {"children": []}}

    async def _fetch_team_roster(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch team roster with player info.

        Params:
            team_id: ESPN team ID (required)

        Returns:
            {"team_roster": {...}} with athletes array
        """
        team_id = params.get("team_id")
        if not team_id:
            logger.warning("team_roster endpoint requires team_id param")
            return {"team_roster": {"athletes": []}}

        url = f"{self.site_api_url}/teams/{team_id}/roster"

        session = await self._get_session()
        try:
            async with session.get(url, proxy=self._proxy) as response:
                if response.status != 200:
                    logger.warning(
                        "Team roster request failed: status=%d, team_id=%s",
                        response.status,
                        team_id,
                    )
                    return {"team_roster": {"athletes": []}}

                data = await response.json()
                return {"team_roster": data}
        except Exception as e:
            logger.error("Error fetching team roster for %s: %s", team_id, e)
            return {"team_roster": {"athletes": []}}

    async def _fetch_team_leaders(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch team statistical leaders (per-game stats by player).

        Params:
            team_id: ESPN team ID (required)
            season_year: Season year (required, e.g., 2026)
            season_type: Season type string (optional, default "regular")

        Returns:
            {"team_leaders": {...}} with categories array containing leader stats
        """
        team_id = params.get("team_id")
        if not team_id:
            logger.warning("team_leaders endpoint requires team_id param")
            return {"team_leaders": {"categories": []}}

        season_year = params.get("season_year")
        if not season_year:
            logger.warning("team_leaders endpoint requires season_year param")
            return {"team_leaders": {"categories": []}}

        season_type_str = params.get("season_type", "regular")
        season_type_map = {"preseason": 1, "regular": 2, "postseason": 3}
        season_type_id = season_type_map.get(season_type_str, 2)

        url = (
            f"{self.core_api_url}/seasons/{season_year}"
            f"/types/{season_type_id}/teams/{team_id}/leaders"
        )

        session = await self._get_session()
        try:
            async with session.get(url, proxy=self._proxy) as response:
                if response.status != 200:
                    logger.warning(
                        "Team leaders request failed: status=%d, team_id=%s",
                        response.status,
                        team_id,
                    )
                    return {"team_leaders": {"categories": []}}

                data = await response.json()
                return {"team_leaders": data}
        except Exception as e:
            logger.error("Error fetching team leaders for %s: %s", team_id, e)
            return {"team_leaders": {"categories": []}}

    async def _fetch_game_roster(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch game-specific roster for a team in an event.

        Uses the core API to get the list of players on the game-day roster,
        including starter and did-not-play flags.

        Params:
            event_id: ESPN event ID (required)
            team_id: ESPN team ID (required)

        Returns:
            {"game_roster": {"entries": [...]}} where each entry has:
            - playerId, displayName (last name), jersey, starter, didNotPlay,
              athlete.$ref, position.$ref
        """
        event_id = params.get("event_id")
        team_id = params.get("team_id")
        if not event_id or not team_id:
            logger.warning("game_roster endpoint requires event_id and team_id params")
            return {"game_roster": {"entries": []}}

        url = (
            f"{self.core_api_url}/events/{event_id}"
            f"/competitions/{event_id}/competitors/{team_id}/roster"
        )

        session = await self._get_session()
        try:
            async with session.get(url, proxy=self._proxy) as response:
                if response.status != 200:
                    logger.warning(
                        "Game roster request failed: status=%d, event=%s, team=%s",
                        response.status,
                        event_id,
                        team_id,
                    )
                    return {"game_roster": {"entries": []}}

                data = await response.json()
                return {"game_roster": data}
        except Exception as e:
            logger.error(
                "Error fetching game roster for event %s team %s: %s",
                event_id,
                team_id,
                e,
            )
            return {"game_roster": {"entries": []}}


def get_espn_game_url(event_id: str, sport: str = "nba") -> str:
    """Generate ESPN game page URL.

    Args:
        event_id: ESPN event ID
        sport: Sport type (e.g., "nba", "nfl")

    Returns:
        ESPN game page URL (e.g., "https://www.espn.com/nba/game/_/gameId/401585123")
    """
    return f"https://www.espn.com/{sport.lower()}/game/_/gameId/{event_id}"
