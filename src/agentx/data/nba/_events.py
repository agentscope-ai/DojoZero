"""NBA-specific event types."""

from dataclasses import dataclass, field

from agentx.data._models import DataEvent, register_event


@register_event
@dataclass(slots=True, frozen=True)
class RawPlayByPlayEvent(DataEvent):
    """Raw NBA play-by-play event from API."""
    
    game_id: str = field(default="")
    points: int = field(default=0)
    home_score: int = field(default=0)
    away_score: int = field(default=0)
    description: str = field(default="")
    
    @property
    def event_type(self) -> str:
        return "raw_play_by_play"


@register_event
@dataclass(slots=True, frozen=True)
class PlayByPlayEvent(DataEvent):
    """Processed NBA play-by-play event."""
    
    game_id: str = field(default="")
    points: int = field(default=0)
    home_score: int = field(default=0)
    away_score: int = field(default=0)
    description: str = field(default="")
    
    @property
    def event_type(self) -> str:
        return "play_by_play"

