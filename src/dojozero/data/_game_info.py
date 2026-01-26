"""Game information models for unified game data across sports.

These Pydantic models provide a structured representation of game data
from ESPN APIs. They are used by:
- Dashboard server for game discovery and scheduling
- Trial builders for populating trial metadata
- Game fetchers (NBA, NFL) for returning typed results
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator

from dojozero.utils import utc_to_us_date


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
    Used by trial builders and dashboard for game discovery.

    Key fields for trial metadata:
        - event_id: ESPN game/event ID (maps to espn_game_id in metadata)
        - home_team.tricode: Home team code (maps to home_tricode)
        - away_team.tricode: Away team code (maps to away_tricode)
        - home_team.name: Full home team name (maps to home_team_name)
        - away_team.name: Full away team name (maps to away_team_name)
        - game_time_utc: Game start time in UTC
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

    def get_game_date_us(self) -> str:
        """Get game date in YYYY-MM-DD format (US Eastern time).

        Returns:
            Date string in YYYY-MM-DD format, or empty string if no game time.
        """
        if self.game_time_utc is None:
            return ""
        return utc_to_us_date(self.game_time_utc)


__all__ = [
    "GameInfo",
    "TeamInfo",
    "VenueInfo",
]
