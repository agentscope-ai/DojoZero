"""Web Search-specific event types."""

from dataclasses import dataclass, field
from typing import Any

from agentx.data._models import DataEvent


@dataclass(slots=True, frozen=True)
class RawWebSearchEvent(DataEvent):
    """Raw web search result event from API."""
    
    query: str = field(default="")
    results: list[dict[str, Any]] = field(default_factory=list)
    
    @property
    def event_type(self) -> str:
        return "raw_web_search"


@dataclass(slots=True, frozen=True)
class WebSearchEvent(DataEvent):
    """Processed web search result event."""
    
    query: str = field(default="")
    results: list[dict[str, Any]] = field(default_factory=list)
    
    @property
    def event_type(self) -> str:
        return "web_search"

