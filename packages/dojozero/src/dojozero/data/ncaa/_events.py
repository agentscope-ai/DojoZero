"""NCAA-specific event types.

NCAA uses two tiers of the event hierarchy:
- Atomic (Tier 1): NCAAPlayEvent — individual play actions
- Snapshot (Tier 3): NCAAGameUpdateEvent — boxscore snapshots

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
# NCAA Stats Models (Pydantic, frozen)
# =============================================================================


class NCAATeamGameStats(BaseModel):
    """NCAA team statistics within a game.

    Structured access to team box score data.
    """

    model_config = ConfigDict(frozen=True)

    team_id: int = 0
    team_name: str = ""
    team_city: str = ""
    team_tricode: str = ""
    score: int = 0


class NCAAPlayerStats(BaseModel):
    """NCAA player statistics within a game."""

    model_config = ConfigDict(frozen=True)

    player_id: int = 0
    name: str = ""
    position: str = ""
    statistics: dict[str, Any] = Field(default_factory=dict)


class NCAAGamePlayerStats(BaseModel):
    """Container for home and away player stats."""

    model_config = ConfigDict(frozen=True)

    home: list[NCAAPlayerStats] = Field(default_factory=list)
    away: list[NCAAPlayerStats] = Field(default_factory=list)


# =============================================================================
# Tier 1: Atomic — NCAAPlayEvent
# =============================================================================


@register_event
class NCAAPlayEvent(BasePlayEvent):
    """NCAA play-by-play event.

    Extends BasePlayEvent with NCAA-specific fields.
    """

    event_type: Literal["event.ncaa_play"] = "event.ncaa_play"

    action_type: str = ""  # "shot", "rebound", "foul", "turnover", etc.
    player_name: str = ""
    player_id: int = 0

    # Legacy field for backward compatibility with old event format
    event_id: str = ""  # Was: {game_id}_pbp_{action_number}
    action_number: int = 0

    def get_dedup_key(self) -> str | None:
        """Return dedup key for NCAA play-by-play events."""
        if self.game_id and self.action_number:
            return f"{self.game_id}_pbp_{self.action_number}"
        return None


# =============================================================================
# Tier 3: Snapshot — NCAAGameUpdateEvent
# =============================================================================


@register_event
class NCAAGameUpdateEvent(BaseGameUpdateEvent):
    """NCAA game update with full boxscore snapshot.

    Contains team stats and player stats as structured Pydantic models
    (not raw dicts).
    """

    home_team_stats: NCAATeamGameStats = Field(default_factory=NCAATeamGameStats)
    away_team_stats: NCAATeamGameStats = Field(default_factory=NCAATeamGameStats)
    event_type: Literal["event.ncaa_game_update"] = "event.ncaa_game_update"

    player_stats: NCAAGamePlayerStats = Field(default_factory=NCAAGamePlayerStats)


__all__ = [
    "NCAAGamePlayerStats",
    "NCAAGameUpdateEvent",
    "NCAAPlayEvent",
    "NCAAPlayerStats",
    "NCAATeamGameStats",
]
