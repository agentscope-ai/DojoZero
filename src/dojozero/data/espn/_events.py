"""ESPN base event types.

These are generic event types that work across all ESPN-supported sports.
Sport-specific modules (nba, nfl) define specialized events that extend
the unified hierarchy in _models.py.

The ESPN-specific lifecycle events (ESPNGameInitializeEvent, etc.) are
retired in favor of the unified events. Legacy event_type strings are
registered for backward compatibility with existing JSONL files.

ESPNTeamInfo and ESPNCompetitor are Pydantic utility types used for ESPN
API parsing.

ESPNGameUpdateEvent and ESPNPlayEvent are generic ESPN implementations
of the event hierarchy, used by the ESPN store for sports that don't have
a dedicated sport-specific store.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dojozero.data._models import (
    BaseGameUpdateEvent,
    BasePlayEvent,
    register_event,
)


# =============================================================================
# Generic ESPN Events (extend hierarchy for generic ESPN usage)
# =============================================================================


@register_event
class ESPNGameUpdateEvent(BaseGameUpdateEvent):
    """Generic ESPN game update event with boxscore data.

    Contains raw team data dicts from ESPN API. Sport-specific stores
    convert these to typed events (NBAGameUpdateEvent, NFLGameUpdateEvent).
    """

    event_type: Literal["event.espn_game_update"] = "event.espn_game_update"

    league: str = ""
    status: str = ""  # Status description
    home_team_data: dict[str, Any] = Field(default_factory=dict)
    away_team_data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


@register_event
class ESPNPlayEvent(BasePlayEvent):
    """Generic ESPN play-by-play event.

    Contains raw play data from ESPN API. Sport-specific stores
    convert these to typed events (NBAPlayEvent, NFLPlayEvent).
    """

    event_type: Literal["event.espn_play"] = "event.espn_play"

    league: str = ""
    play_type: str = ""  # Type of play
    team_abbreviation: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Utility Types for ESPN API parsing
# =============================================================================


class ESPNTeamInfo(BaseModel):
    """Basic team information from ESPN API response.

    Used during API parsing to extract team data before converting
    to the canonical TeamIdentity model.
    """

    model_config = ConfigDict(frozen=True)

    team_id: str = ""
    name: str = ""  # Full display name
    abbreviation: str = ""
    location: str = ""  # City/location
    color: str = ""  # Primary color hex
    alternate_color: str = ""
    logo_url: str = ""

    @classmethod
    def from_espn_dict(cls, data: dict[str, Any]) -> "ESPNTeamInfo":
        """Create from ESPN API team dict."""
        return cls(
            team_id=str(data.get("id", "")),
            name=data.get("displayName", ""),
            abbreviation=data.get("abbreviation", ""),
            location=data.get("location", ""),
            color=data.get("color", ""),
            alternate_color=data.get("alternateColor", ""),
            logo_url=data.get("logo", ""),
        )


class ESPNCompetitor(BaseModel):
    """Competitor (team) in a game with score.

    Used during ESPN API parsing to extract competitor data.
    """

    model_config = ConfigDict(frozen=True)

    team: ESPNTeamInfo = Field(default_factory=ESPNTeamInfo)
    home_away: str = ""  # "home" or "away"
    score: int = 0
    winner: bool = False
    records: list[dict[str, Any]] = Field(default_factory=list)
    line_scores: list[int] = Field(default_factory=list)

    @classmethod
    def from_espn_dict(cls, data: dict[str, Any]) -> "ESPNCompetitor":
        """Create from ESPN API competitor dict."""
        team_data = data.get("team", {})
        line_scores = [
            int(ls.get("value", 0) or 0)
            for ls in data.get("linescores", [])
            if isinstance(ls, dict)
        ]
        return cls(
            team=ESPNTeamInfo.from_espn_dict(team_data),
            home_away=data.get("homeAway", ""),
            score=int(data.get("score", 0) or 0),
            winner=bool(data.get("winner", False)),
            records=data.get("records", []),
            line_scores=line_scores,
        )


__all__ = [
    "ESPNCompetitor",
    "ESPNGameUpdateEvent",
    "ESPNPlayEvent",
    "ESPNTeamInfo",
]
