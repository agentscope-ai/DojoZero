"""Game Discovery for DojoZero Dashboard Server.

Provides unified interfaces for fetching game information from
NBA API and ESPN API (for NFL).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator

LOGGER = logging.getLogger("dojozero.game_discovery")


class TeamInfo(BaseModel):
    """Team information from ESPN API.

    Captures all team data returned by ESPN to avoid additional API calls in the UI.
    """

    model_config = {"populate_by_name": True}

    team_id: str = Field(default="", alias="teamId")
    name: str = Field(default="", alias="displayName")
    tricode: str = Field(default="", alias="teamTricode")
    score: int = 0
    location: str = Field(default="", alias="teamCity")
    short_name: str = Field(default="", alias="shortDisplayName")
    color: str = ""
    alternate_color: str = Field(default="", alias="alternateColor")
    logo: str = ""
    record: str = ""

    @field_validator("team_id", mode="before")
    @classmethod
    def coerce_team_id(cls, v: Any) -> str:
        return str(v) if v is not None else ""

    @field_validator("score", mode="before")
    @classmethod
    def coerce_score(cls, v: Any) -> int:
        if v is None:
            return 0
        if isinstance(v, str):
            return int(v) if v else 0
        return int(v)

    @field_validator("name", mode="before")
    @classmethod
    def coerce_name(cls, v: Any, info: Any) -> str:
        if v:
            return str(v)
        # Fall back to teamCity + teamName if displayName not provided
        data = info.data if hasattr(info, "data") else {}
        city = data.get("teamCity", "") or data.get("location", "")
        team_name = data.get("teamName", "")
        return f"{city} {team_name}".strip() if city or team_name else ""

    @field_validator("short_name", mode="before")
    @classmethod
    def coerce_short_name(cls, v: Any, info: Any) -> str:
        if v:
            return str(v)
        # Fall back to teamName if shortDisplayName not provided
        data = info.data if hasattr(info, "data") else {}
        return data.get("teamName", "") or ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=False)


class VenueInfo(BaseModel):
    """Venue information from ESPN API."""

    model_config = {"populate_by_name": True}

    venue_id: str = Field(default="", alias="venueId")
    name: str = ""
    city: str = ""
    state: str = ""
    indoor: bool = True

    @field_validator("venue_id", mode="before")
    @classmethod
    def coerce_venue_id(cls, v: Any) -> str:
        return str(v) if v is not None else ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=False)


class GameInfo(BaseModel):
    """Unified game information across sports.

    Captures all game data from ESPN to avoid additional API calls in the UI.
    """

    model_config = {"populate_by_name": True}

    event_id: str = Field(default="", alias="gameId")
    sport_type: str = ""
    status: int = Field(default=1, alias="gameStatus")
    status_text: str = Field(default="", alias="gameStatusText")
    game_time_utc: datetime | None = Field(default=None, alias="gameTimeUTC")
    home_team: TeamInfo = Field(default_factory=TeamInfo, alias="homeTeam")
    away_team: TeamInfo = Field(default_factory=TeamInfo, alias="awayTeam")
    venue: VenueInfo = Field(default_factory=VenueInfo)
    broadcasts: list[dict[str, Any]] = Field(default_factory=list)
    broadcast: str = ""
    name: str = ""
    short_name: str = Field(default="", alias="shortName")
    odds: dict[str, Any] = Field(default_factory=dict)
    period: int = 0
    clock: str = Field(default="", alias="gameClock")
    attendance: int = 0
    neutral_site: bool = Field(default=False, alias="neutralSite")
    season_year: int = Field(default=0, alias="seasonYear")
    season_type: str = Field(default="", alias="seasonType")

    @field_validator("event_id", mode="before")
    @classmethod
    def coerce_event_id(cls, v: Any) -> str:
        return str(v) if v is not None else ""

    @field_validator("status", mode="before")
    @classmethod
    def coerce_status(cls, v: Any) -> int:
        if v is None:
            return 1
        return int(v)

    @field_validator("game_time_utc", mode="before")
    @classmethod
    def parse_game_time(cls, v: Any) -> datetime | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            if v.tzinfo is None:
                return v.replace(tzinfo=timezone.utc)
            return v
        if isinstance(v, str) and v:
            from dateutil import parser

            try:
                dt = parser.parse(v)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                return None
        return None

    @field_validator("home_team", "away_team", mode="before")
    @classmethod
    def parse_team(cls, v: Any) -> TeamInfo:
        if isinstance(v, TeamInfo):
            return v
        if isinstance(v, dict):
            return TeamInfo.model_validate(v)
        return TeamInfo()

    @field_validator("venue", mode="before")
    @classmethod
    def parse_venue(cls, v: Any) -> VenueInfo:
        if isinstance(v, VenueInfo):
            return v
        if isinstance(v, dict):
            return VenueInfo.model_validate(v)
        return VenueInfo()

    def to_dict(self) -> dict[str, Any]:
        result = self.model_dump(by_alias=False)
        # Format datetime as ISO string
        if result.get("game_time_utc"):
            result["game_time_utc"] = result["game_time_utc"].isoformat()
        # Nested models need to be converted to dicts
        result["home_team"] = self.home_team.to_dict()
        result["away_team"] = self.away_team.to_dict()
        result["venue"] = self.venue.to_dict()
        return result


class NBAGameFetcher:
    """Fetches NBA game information from NBA API."""

    async def fetch_games_for_date(
        self,
        date: str | None = None,
    ) -> list[GameInfo]:
        """Fetch NBA games for a specific date.

        Args:
            date: Date in YYYY-MM-DD format. If None, uses today.

        Returns:
            List of GameInfo objects.
        """
        import asyncio

        from dojozero.data.nba._utils import get_games_for_date

        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        try:
            # Run in thread pool to avoid blocking the event loop
            games_raw = await asyncio.to_thread(get_games_for_date, date, False)
        except Exception as e:
            LOGGER.error("Error fetching NBA games for %s: %s", date, e)
            return []

        games: list[GameInfo] = []
        for g in games_raw:
            # Add sport_type and generate short_name if missing
            g["sport_type"] = "nba"
            if not g.get("shortName"):
                home = g.get("homeTeam", {})
                away = g.get("awayTeam", {})
                g["shortName"] = (
                    f"{away.get('teamTricode', '')} @ {home.get('teamTricode', '')}"
                )

            # Use Pydantic to parse and validate
            game = GameInfo.model_validate(g)
            games.append(game)

        return games

    async def fetch_games_for_date_range(
        self,
        start_date: str,
        end_date: str,
    ) -> list[GameInfo]:
        """Fetch NBA games for a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            List of GameInfo objects.
        """
        games: list[GameInfo] = []
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        if start > end:
            start, end = end, start

        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            day_games = await self.fetch_games_for_date(date_str)
            games.extend(day_games)
            current += timedelta(days=1)

        return games

    async def get_game_status(
        self, game_id: str, game_date: str | None = None
    ) -> int | None:
        """Get current status of a game.

        Args:
            game_id: ESPN event ID
            game_date: Date of the game (YYYY-MM-DD). If None, searches recent dates.

        Returns:
            Game status (1=scheduled, 2=in_progress, 3=finished) or None if not found.
        """
        import asyncio

        from dojozero.data.nba._utils import get_games_for_date

        if game_date:
            dates_to_check = [game_date]
        else:
            # Check today and yesterday
            today = datetime.now()
            dates_to_check = [
                today.strftime("%Y-%m-%d"),
                (today - timedelta(days=1)).strftime("%Y-%m-%d"),
            ]

        for date in dates_to_check:
            try:
                # Run in thread pool to avoid blocking the event loop
                games = await asyncio.to_thread(get_games_for_date, date, False)
                for g in games:
                    if str(g.get("gameId")) == game_id:
                        return g.get("gameStatus")
            except Exception as e:
                LOGGER.warning(
                    "Error checking game status for %s on %s: %s", game_id, date, e
                )

        return None


class NFLGameFetcher:
    """Fetches NFL game information from ESPN API."""

    async def fetch_games_for_date(
        self,
        date: str | None = None,
    ) -> list[GameInfo]:
        """Fetch NFL games for a specific date.

        Args:
            date: Date in YYYY-MM-DD format. If None, fetches current scoreboard
                  (which includes upcoming games for the current week).

        Returns:
            List of GameInfo objects.
        """
        from dojozero.data.nfl._api import NFLExternalAPI

        api = NFLExternalAPI()
        try:
            if date is None:
                # Fetch current scoreboard without date filter (includes upcoming games)
                data = await api.fetch("scoreboard")
            else:
                # Parse date to ESPN format (YYYYMMDD)
                try:
                    parsed_date = datetime.strptime(date, "%Y-%m-%d")
                    date_str = parsed_date.strftime("%Y%m%d")
                except ValueError:
                    LOGGER.error("Invalid date format: %s", date)
                    return []
                data = await api.fetch("scoreboard", {"dates": date_str})
            return self._parse_scoreboard(data)
        except Exception as e:
            LOGGER.error("Error fetching NFL games for %s: %s", date, e)
            return []
        finally:
            await api.close()

    async def fetch_games_for_week(
        self,
        week: int,
    ) -> list[GameInfo]:
        """Fetch NFL games for a specific week.

        Args:
            week: Week number (1-18 for regular season)

        Returns:
            List of GameInfo objects.
        """
        from dojozero.data.nfl._api import NFLExternalAPI

        api = NFLExternalAPI()
        try:
            data = await api.fetch("scoreboard", {"week": week})
            return self._parse_scoreboard(data)
        except Exception as e:
            LOGGER.error("Error fetching NFL games for week %d: %s", week, e)
            return []
        finally:
            await api.close()

    def _parse_scoreboard(self, data: dict[str, Any]) -> list[GameInfo]:
        """Parse ESPN scoreboard response into GameInfo objects."""
        scoreboard = data.get("scoreboard", {})
        events = scoreboard.get("events", [])

        games: list[GameInfo] = []
        for event in events:
            competitions = event.get("competitions", [])
            if not competitions:
                continue

            comp = competitions[0]
            status = comp.get("status", {})
            status_type = status.get("type", {})

            # Get competitors and convert to our format
            competitors = comp.get("competitors", [])
            home_team_data: dict[str, Any] = {}
            away_team_data: dict[str, Any] = {}
            for c in competitors:
                team = c.get("team", {})
                # Extract record from competitor records array
                records = c.get("records", [])
                record = records[0].get("summary", "") if records else ""

                team_data = {
                    "teamId": team.get("id", ""),
                    "displayName": team.get("displayName", ""),
                    "teamTricode": team.get("abbreviation", ""),
                    "score": c.get("score", "0"),
                    "teamCity": team.get("location", ""),
                    "shortDisplayName": team.get("shortDisplayName", ""),
                    "color": team.get("color", ""),
                    "alternateColor": team.get("alternateColor", ""),
                    "logo": team.get("logo", ""),
                    "record": record,
                }
                if c.get("homeAway") == "home":
                    home_team_data = team_data
                else:
                    away_team_data = team_data

            # Get venue info
            venue_data = comp.get("venue", {})
            venue_address = venue_data.get("address", {})
            venue = {
                "venueId": str(venue_data.get("id", "")),
                "name": venue_data.get("fullName", ""),
                "city": venue_address.get("city", ""),
                "state": venue_address.get("state", ""),
                "indoor": venue_data.get("indoor", True),
            }

            # Get broadcast info
            broadcasts_raw = comp.get("broadcasts", [])
            broadcasts: list[dict[str, Any]] = []
            broadcast_names: list[str] = []
            for b in broadcasts_raw:
                market = b.get("market", "")
                names = b.get("names", [])
                broadcasts.append({"market": market, "names": names})
                if names:
                    broadcast_names.extend(names)
            broadcast = ", ".join(broadcast_names) if broadcast_names else ""

            # Get odds
            odds_list = comp.get("odds", [])
            odds: dict[str, Any] = {}
            if odds_list:
                o = odds_list[0]
                odds = {
                    "provider": o.get("provider", {}).get("name", ""),
                    "spread": o.get("spread", 0),
                    "overUnder": o.get("overUnder", 0),
                    "homeMoneyLine": o.get("homeTeamOdds", {}).get("moneyLine", 0),
                    "awayMoneyLine": o.get("awayTeamOdds", {}).get("moneyLine", 0),
                }

            # Get season info
            season = event.get("season", {})
            season_type_id = season.get("type", 0)
            season_type_map = {
                1: "preseason",
                2: "regular",
                3: "postseason",
                4: "offseason",
            }

            # Build game data dict for Pydantic validation
            game_data = {
                "gameId": event.get("id", ""),
                "sport_type": "nfl",
                "gameStatus": int(status_type.get("id", "1")),
                "gameStatusText": status_type.get("description", "Scheduled"),
                "gameTimeUTC": comp.get("date", ""),
                "homeTeam": home_team_data,
                "awayTeam": away_team_data,
                "venue": venue,
                "broadcasts": broadcasts,
                "broadcast": broadcast,
                "name": event.get("name", ""),
                "shortName": event.get("shortName", ""),
                "odds": odds,
                "period": status.get("period", 0),
                "gameClock": status.get("displayClock", ""),
                "attendance": comp.get("attendance", 0),
                "neutralSite": comp.get("neutralSite", False),
                "seasonYear": season.get("year", 0),
                "seasonType": season_type_map.get(season_type_id, ""),
            }

            game = GameInfo.model_validate(game_data)
            games.append(game)

        return games

    async def get_game_status(
        self, event_id: str, game_date: str | None = None
    ) -> int | None:
        """Get current status of a game.

        Args:
            event_id: ESPN event ID
            game_date: Date of the game (YYYY-MM-DD).

        Returns:
            Game status (1=scheduled, 2=in_progress, 3=finished) or None if not found.
        """
        if game_date:
            games = await self.fetch_games_for_date(game_date)
            for g in games:
                if g.event_id == event_id:
                    return g.status

        # If no date provided or not found, try current scoreboard
        from dojozero.data.nfl._api import NFLExternalAPI

        api = NFLExternalAPI()
        try:
            data = await api.fetch("scoreboard")
            games = self._parse_scoreboard(data)
            for g in games:
                if g.event_id == event_id:
                    return g.status
        except Exception as e:
            LOGGER.warning("Error checking game status for %s: %s", event_id, e)
        finally:
            await api.close()

        return None


__all__ = [
    "GameInfo",
    "NBAGameFetcher",
    "NFLGameFetcher",
    "TeamInfo",
    "VenueInfo",
]
