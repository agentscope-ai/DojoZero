"""Polymarket-specific processors."""

from typing import Sequence

from agentx.data._models import DataEvent
from agentx.data._processors import DataProcessor
from agentx.data.polymarket._events import OddsChangeEvent, RawOddsChangeEvent


class OddsChangeProcessor(DataProcessor):
    """Processor that transforms raw odds change events to cooked events."""
    
    async def process(self, events: Sequence[DataEvent]) -> DataEvent | None:
        """Process raw odds change events.
        
        Args:
            events: Sequence of raw odds change events
            
        Returns:
            Processed odds change event or None
        """
        if not events:
            return None
        
        # Get the latest raw event
        raw_event = None
        for event in events:
            if isinstance(event, RawOddsChangeEvent):
                raw_event = event
                break
        
        if not raw_event:
            return None
        
        # Transform to cooked event (could add validation, enrichment, etc.)
        return OddsChangeEvent(
            timestamp=raw_event.timestamp,
            market_id=raw_event.market_id,
            outcomes=raw_event.outcomes,
        )

