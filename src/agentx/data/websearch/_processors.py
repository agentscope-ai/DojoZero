"""Web Search-specific processors."""

from __future__ import annotations

from typing import Sequence, cast

from agentx.data._models import DataEvent
from agentx.data._processors import DataProcessor
from agentx.data.websearch._events import RawWebSearchEvent, WebSearchEvent


class WebSearchProcessor(DataProcessor):
    """Processor that transforms raw web search events to cooked events."""
    
    async def process(self, events: Sequence[DataEvent]) -> DataEvent | None:
        """Process raw web search events.
        
        Args:
            events: Sequence of raw web search events
            
        Returns:
            Processed web search event or None
        """
        if not events:
            return None
        
        # Get the latest raw event by checking event_type (avoids isinstance type issues with decorator)
        # Type checker sees decorator return type, but at runtime it's the actual class
        raw_event: RawWebSearchEvent | None = None  # type: ignore[valid-type]
        for event in events:
            if event.event_type == "raw_web_search":
                raw_event = cast(RawWebSearchEvent, event)  # type: ignore[arg-type]
                break
        
        if not raw_event:
            return None
        
        # Transform to cooked event (could add validation, filtering, etc.)
        # WebSearchEvent is a dataclass, all fields have defaults
        # Type checker issue: decorator makes it see function instead of class
        event_class: type[WebSearchEvent] = WebSearchEvent  # type: ignore[assignment]
        return event_class(
            timestamp=raw_event.timestamp,
            query=raw_event.query,
            results=raw_event.results,
        )

