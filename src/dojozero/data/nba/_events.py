"""NBA-specific event types.

NBA uses two tiers of the event hierarchy:
- Atomic (Tier 1): NBAPlayEvent — individual play actions
- Snapshot (Tier 3): NBAGameUpdateEvent — boxscore snapshots

Lifecycle events (GameInitializeEvent, GameStartEvent, GameResultEvent)
and OddsUpdateEvent are unified across sports and defined in _models.py.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dojozero.data._models import (
    BaseGameUpdateEvent,
    BasePlayEvent,
    register_event,
)


# =============================================================================
# NBA Stats Models (Pydantic, frozen)
# =============================================================================


class NBATeamGameStats(BaseModel):
    """NBA team statistics within a game.

    Structured access to team box score data.
    """

    model_config = ConfigDict(frozen=True)

    team_id: int = 0
    team_name: str = ""
    team_city: str = ""
    team_tricode: str = ""
    score: int = 0
    wins: int = 0
    losses: int = 0
    seed: int = 0
    timeouts_remaining: int = 0
    in_bonus: bool | None = None
    periods: list[dict[str, Any]] = Field(default_factory=list)


class NBAPlayerStats(BaseModel):
    """NBA player statistics within a game."""

    model_config = ConfigDict(frozen=True)

    player_id: int = 0
    name: str = ""
    statistics: dict[str, Any] = Field(default_factory=dict)


class NBAGamePlayerStats(BaseModel):
    """Container for home and away player stats."""

    model_config = ConfigDict(frozen=True)

    home: list[NBAPlayerStats] = Field(default_factory=list)
    away: list[NBAPlayerStats] = Field(default_factory=list)


# =============================================================================
# Tier 1: Atomic — NBAPlayEvent
# =============================================================================


@register_event
class NBAPlayEvent(BasePlayEvent):
    """NBA play-by-play event.

    Extends BasePlayEvent with NBA-specific fields.
    """

    event_type: Literal["event.nba_play"] = "event.nba_play"

    action_type: str = ""  # "shot", "rebound", "foul", "turnover", etc.
    player_name: str = ""
    player_id: int = 0

    # Legacy field for backward compatibility with old event format
    event_id: str = ""  # Was: {game_id}_pbp_{action_number}
    action_number: int = 0


# =============================================================================
# Tier 3: Snapshot — NBAGameUpdateEvent
# =============================================================================


@register_event
class NBAGameUpdateEvent(BaseGameUpdateEvent):
    """NBA game update with full boxscore snapshot.

    Contains team stats and player stats as structured Pydantic models
    (not raw dicts).
    """

    home_team_stats: NBATeamGameStats = Field(default_factory=NBATeamGameStats)
    away_team_stats: NBATeamGameStats = Field(default_factory=NBATeamGameStats)
    event_type: Literal["event.nba_game_update"] = "event.nba_game_update"

    player_stats: NBAGamePlayerStats = Field(default_factory=NBAGamePlayerStats)


__all__ = [
    "NBAGamePlayerStats",
    "NBAGameUpdateEvent",
    "NBAPlayEvent",
    "NBAPlayerStats",
    "NBATeamGameStats",
]
