"""Web Search-specific event types."""

from dataclasses import dataclass, field
from typing import Any

from agentx.data._models import DataEvent, register_event


@register_event
@dataclass(slots=True, frozen=True)
class RawWebSearchEvent(DataEvent):
    """Raw web search result event from API."""
    
    query: str = field(default="")
    results: list[dict[str, Any]] = field(default_factory=list)
    
    @property
    def event_type(self) -> str:
        return "raw_web_search"


@register_event
@dataclass(slots=True, frozen=True)
class InjurySummaryEvent(DataEvent):
    """Injury summary event generated from web search results.
    
    Hybrid format: contains both human-readable summary and
    structured machine-readable data (team -> list of injured players).
    """
    
    query: str = field(default="")
    summary: str = field(default="")
    injured_players: dict[str, list[str]] = field(default_factory=dict)
    
    @property
    def event_type(self) -> str:
        return "injury_summary"

