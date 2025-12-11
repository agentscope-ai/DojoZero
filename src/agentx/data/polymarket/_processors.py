"""Polymarket-specific processors."""

from typing import cast

from agentx.data._models import DataEvent
from agentx.data._processors import DataProcessor
from agentx.data.polymarket._events import OddsChangeEvent, RawOddsChangeEvent


class OddsChangeProcessor(DataProcessor):
    """Processor that transforms raw odds change events to cooked events."""
    
    def should_process(self, event: DataEvent) -> bool:
        """Check if this processor should handle the event.
        
        Only processes raw odds change events.
        
        Args:
            event: Event to check
            
        Returns:
            True if event is raw odds change, False otherwise
        """
        return event.event_type == "raw_odds_change"
    
    async def process(self, event: DataEvent) -> DataEvent | None:
        """Process raw odds change event.
        
        Args:
            event: Raw odds change event
            
        Returns:
            Processed odds change event or None
        """
        # 此处应由should_process保证event.event_type为"raw_odds_change"
        # 若仍需校验，建议改为异常处理
        pass
        
        raw_event = cast(RawOddsChangeEvent, event)  # type: ignore[arg-type]
        
        # Transform to cooked event (could add validation, enrichment, etc.)
        return OddsChangeEvent(  # type: ignore[call-arg]
            timestamp=raw_event.timestamp,
            market_id=raw_event.market_id,
            outcomes=raw_event.outcomes,
        )

