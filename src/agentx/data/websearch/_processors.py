"""Web Search-specific processors."""

from typing import Sequence

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
        
        # Get the latest raw event
        raw_event = None
        for event in events:
            if isinstance(event, RawWebSearchEvent):
                raw_event = event
                break
        
        if not raw_event:
            return None
        
        # Transform to cooked event (could add validation, filtering, etc.)
        return WebSearchEvent(
            timestamp=raw_event.timestamp,
            query=raw_event.query,
            results=raw_event.results,
        )

