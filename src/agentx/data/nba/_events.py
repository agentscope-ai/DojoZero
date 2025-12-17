"""NBA-specific event types."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

from agentx.data._models import DataEvent, EventTypes, register_event


@register_event
@dataclass(slots=True, frozen=True)
class PlayByPlayEvent(DataEvent):
    """Processed NBA play-by-play event.
    
    Contains detailed information about a single play-by-play action,
    including event type, player info, scores, and description.
    """
    
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default="")  # Unique event ID: {game_id}_pbp_{action_number}
    game_id: str = field(default="")
    action_type: str = field(default="")  # Action type string (e.g., "rebound", "shot", "foul", "turnover", "substitution", "timeout")
    action_number: int = field(default=0)
    period: int = field(default=0)
    clock: str = field(default="")
    person_id: int = field(default=0)  # Player ID
    player_name: str = field(default="")  # Player name (if available)
    team_tricode: str = field(default="")
    home_score: int = field(default=0)
    away_score: int = field(default=0)
    description: str = field(default="")
    
    @property
    def event_type(self) -> str:
        return EventTypes.PLAY_BY_PLAY.value


# =============================================================================
# Game Events
# =============================================================================


@register_event
@dataclass(slots=True, frozen=True)
class GameStartEvent(DataEvent):
    """Game start event signaling transition from pregame to live."""
    
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default="")
    
    @property
    def event_type(self) -> str:
        return "game_start"


@register_event
@dataclass(slots=True, frozen=True)
class GameResultEvent(DataEvent):
    """Game result event with winner and final score."""
    
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default="")
    winner: str = field(default="")  # "home" or "away"
    final_score: dict[str, int] = field(default_factory=dict)  # {"home": 100, "away": 95}
    
    @property
    def event_type(self) -> str:
        return "game_result"


@register_event
@dataclass(slots=True, frozen=True)
class GameUpdateEvent(DataEvent):
    """Game update event with full scoreboard snapshot.
    
    Contains:
    - Team basic info (name, city, tricode, score, wins, losses)
    - Team stats (period scores, timeouts, bonus status)
    - All player stats (complete list of players with their statistics)
    
    Note: Game status (start/end) is handled by GameStartEvent and GameResultEvent from PlayByPlay.
    """
    
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default="")
    game_id: str = field(default="")
    period: int = field(default=0)
    game_clock: str = field(default="")
    game_time_utc: str = field(default="")  # ISO format datetime string
    home_team: dict[str, Any] = field(
        default_factory=dict
    )  # Team info: teamId, teamName, teamCity, teamTricode, score, wins, losses, seed, timeoutsRemaining, inBonus, periods (quarter scores)
    away_team: dict[str, Any] = field(
        default_factory=dict
    )  # Team info: teamId, teamName, teamCity, teamTricode, score, wins, losses, seed, timeoutsRemaining, inBonus, periods (quarter scores)
    player_stats: dict[str, Any] = field(
        default_factory=dict
    )  # All player stats: {"home": [list of player dicts with statistics], "away": [list of player dicts with statistics]}
    
    @property
    def event_type(self) -> str:
        return "game_update"



