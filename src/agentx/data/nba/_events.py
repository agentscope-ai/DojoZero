"""NBA-specific event types."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

from agentx.data._models import DataEvent, EventTypes, register_event


# =============================================================================
# Play-by-Play Action Types
# =============================================================================

# Critical action types that should be emitted even when filtering
# Based on NBA API actionType field (string values)
CRITICAL_ACTION_TYPES = {
    "substitution",  # May indicate injury or strategic change
    "timeout",  # Injury timeouts, strategic timeouts
    "ejection",  # Player removed from game
    "foul",  # All fouls are critical (technical, flagrant, personal, fouling out)
    "turnover",  # Turnovers can cause game swings, especially in clutch moments
    "violation",  # Violations (shot clock, 8-second, 3-second, etc.) can be critical
}


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
    
    def is_critical(self) -> bool:
        """Check if this event is critical (injuries, ejections, fouls, turnovers, violations, etc.).
        
        Critical events include:
        - ALL fouls (personal, technical, flagrant, fouling out)
        - ALL injuries (explicit mentions or suspicious substitutions/timeouts)
        - ALL ejections
        - Turnovers (can cause game swings, especially in clutch moments)
        - Violations (shot clock, 8-second, 3-second, etc.)
        - Jump balls (can be critical in clutch moments)
        - Substitutions (may indicate injury or strategic change)
        - Timeouts (injury timeouts, strategic timeouts)
        """
        action_type_lower = self.action_type.lower()
        desc_lower = self.description.lower()
        
        # Check action type - if it's in critical types, it's automatically critical
        if action_type_lower in CRITICAL_ACTION_TYPES:
            return True
        
        # Check for specific critical event types using actionType and description
        if self._is_injury_related(desc_lower):
            return True
        
        if self._is_ejection_related(action_type_lower, desc_lower):
            return True
        
        if self._is_foul_related(action_type_lower, desc_lower):
            return True
        
        if self._is_turnover_related(action_type_lower):
            return True
        
        if self._is_violation_related(action_type_lower, desc_lower):
            return True
        
        return False
    
    def _is_injury_related(self, desc_lower: str) -> bool:
        """Check if event is injury-related using actionType and description.
        
        Args:
            desc_lower: Lowercase description text
            
        Returns:
            True if injury-related (substitution or timeout with injury keywords, or description keywords)
        """
        action_type_lower = self.action_type.lower()
        
        # Check if action type suggests injury (substitution or timeout)
        if action_type_lower in ["substitution", "timeout"]:
            injury_keywords = [
                "injury", "injured", "hurt", "leaves game", "leaves the game",
                "medical", "trainer", "limping", "limped", "assistance",
                "helped off", "carried off", "stretcher", "walking off",
                "limping off", "holding", "clutching", "grimacing"
            ]
            if any(keyword in desc_lower for keyword in injury_keywords):
                return True
        
        # Also check description for injury keywords regardless of action type
        injury_keywords = [
            "injury", "injured", "hurt", "leaves game", "leaves the game",
            "medical", "trainer", "limping", "limped", "assistance",
            "helped off", "carried off", "stretcher", "walking off",
            "limping off", "holding", "clutching", "grimacing"
        ]
        return any(keyword in desc_lower for keyword in injury_keywords)
    
    def _is_ejection_related(self, action_type_lower: str, desc_lower: str) -> bool:
        """Check if event is ejection-related using actionType and description.
        
        Args:
            action_type_lower: Lowercase actionType string
            desc_lower: Lowercase description text
            
        Returns:
            True if ejection-related (actionType="ejection" or description keywords)
        """
        if action_type_lower == "ejection":
            return True
        
        ejection_keywords = [
            "ejected", "ejection", "thrown out", "removed from game"
        ]
        return any(keyword in desc_lower for keyword in ejection_keywords)
    
    def _is_foul_related(self, action_type_lower: str, desc_lower: str) -> bool:
        """Check if event is foul-related using actionType and description.
        
        Args:
            action_type_lower: Lowercase actionType string
            desc_lower: Lowercase description text
            
        Returns:
            True if foul-related (actionType="foul" or description keywords for technical/flagrant)
        """
        if action_type_lower == "foul":
            return True
        
        # Check for technical fouls (even if not actionType="foul")
        technical_keywords = [
            "technical", "tech", "unsportsmanlike", "disrespectful",
            "taunting", "arguing", "disputing"
        ]
        if any(keyword in desc_lower for keyword in technical_keywords):
            return True
        
        # Check for flagrant fouls (even if not actionType="foul")
        if "flagrant" in desc_lower:
            return True
        
        # Check for fouling out (5+ fouls) - always critical
        fouling_out_keywords = [
            "fouled out", "fouls out", "6th foul", "sixth foul",
            "fifth foul", "5th foul", "disqualified"
        ]
        return any(keyword in desc_lower for keyword in fouling_out_keywords)
    
    def _is_turnover_related(self, action_type_lower: str) -> bool:
        """Check if event is turnover-related using actionType.
        
        Args:
            action_type_lower: Lowercase actionType string
            
        Returns:
            True if turnover-related (actionType="turnover")
        """
        return action_type_lower == "turnover"
    
    def _is_violation_related(self, action_type_lower: str, desc_lower: str) -> bool:
        """Check if event is violation-related using actionType and description.
        
        Args:
            action_type_lower: Lowercase actionType string
            desc_lower: Lowercase description text
            
        Returns:
            True if violation-related (actionType="violation" or description keywords)
        """
        if action_type_lower == "violation":
            return True
        
        violation_keywords = [
            "shot clock", "8-second", "eight-second", "3-second", "three-second",
            "24-second", "twenty-four-second", "backcourt", "traveling", "carrying"
        ]
        return any(keyword in desc_lower for keyword in violation_keywords)
    
    def get_critical_type(self) -> str | None:
        """Get the type of critical event (injury, ejection, foul, turnover, violation, etc.).
        
        Returns:
            String describing the critical event type, or None if not critical
        """
        if not self.is_critical():
            return None
        
        action_type_lower = self.action_type.lower()
        desc_lower = self.description.lower()
        
        if self._is_injury_related(desc_lower):
            return "injury"
        if self._is_ejection_related(action_type_lower, desc_lower):
            return "ejection"
        if self._is_foul_related(action_type_lower, desc_lower):
            return "foul"
        if self._is_turnover_related(action_type_lower):
            return "turnover"
        if self._is_violation_related(action_type_lower, desc_lower):
            return "violation"
        if action_type_lower == "substitution":
            return "substitution"
        if action_type_lower == "timeout":
            return "timeout"
        if action_type_lower == "jumpball":
            return "jump_ball"
        
        return "other"


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
    - Game status and timing info
    - Team basic info (name, city, tricode, score, wins, losses)
    - Team stats (period scores, timeouts, bonus status)
    - Game leaders (top scorer, rebounder, assist leader for each team)
    """
    
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default="")
    game_id: str = field(default="")
    game_status: int = field(default=0)  # 1=Not Started, 2=In Progress, 3=Finished
    game_status_text: str = field(default="")
    period: int = field(default=0)
    game_clock: str = field(default="")
    game_time_utc: str = field(default="")  # ISO format datetime string
    home_team: dict[str, Any] = field(
        default_factory=dict
    )  # Team info: teamId, teamName, teamCity, teamTricode, score, wins, losses, seed, timeoutsRemaining, inBonus, periods (quarter scores)
    away_team: dict[str, Any] = field(
        default_factory=dict
    )  # Team info: teamId, teamName, teamCity, teamTricode, score, wins, losses, seed, timeoutsRemaining, inBonus, periods (quarter scores)
    game_leaders: dict[str, Any] = field(
        default_factory=dict
    )  # Game leaders: home (points, rebounds, assists leaders) and away (points, rebounds, assists leaders) with player names and stats
    
    @property
    def event_type(self) -> str:
        return "game_update"


@register_event
@dataclass(slots=True, frozen=True)
class InGameCriticalEvent(DataEvent):
    """In-game critical event detected from play-by-play data.
    
    Emitted when a critical event is detected during a live game, including:
    - Injuries
    - Ejections
    - Fouls (technical, flagrant, fouling out)
    - Turnovers
    - Violations
    - Substitutions (potentially injury-related)
    - Timeouts (potentially injury-related)
    - Jump balls
    
    The critical_type field indicates the specific type of critical event.
    """
    
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default="")  # Unique event ID
    game_id: str = field(default="")
    critical_type: str = field(default="")  # Type: "injury", "ejection", "foul", "turnover", "violation", "substitution", "timeout", "jump_ball", "other"
    period: int = field(default=0)
    clock: str = field(default="")
    player_id: int = field(default=0)
    player_name: str = field(default="")
    team_tricode: str = field(default="")
    description: str = field(default="")  # Original play-by-play description
    action_type: str = field(default="")  # ActionType string that triggered this
    action_number: int = field(default=0)  # Play-by-play action number
    
    @property
    def event_type(self) -> str:
        return EventTypes.IN_GAME_CRITICAL.value

