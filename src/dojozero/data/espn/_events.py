"""ESPN base event types.

These are generic event types that work across all ESPN-supported sports.
Sport-specific modules (nba, nfl) define specialized events that extend
the unified hierarchy in _models.py.

The ESPN-specific lifecycle events (ESPNGameInitializeEvent, etc.) are
retired in favor of the unified events. Legacy event_type strings are
registered for backward compatibility with existing JSONL files.

ESPNGameUpdateEvent and ESPNPlayEvent are generic ESPN implementations
of the event hierarchy, used by the ESPN store for sports that don't have
a dedicated sport-specific store.
"""

from typing import Any, Literal

from pydantic import Field as PydanticField

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
    home_team_data: dict[str, Any] = PydanticField(default_factory=dict)
    away_team_data: dict[str, Any] = PydanticField(default_factory=dict)
    metadata: dict[str, Any] = PydanticField(default_factory=dict)


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
    metadata: dict[str, Any] = PydanticField(default_factory=dict)


__all__ = [
    "ESPNGameUpdateEvent",
    "ESPNPlayEvent",
]
