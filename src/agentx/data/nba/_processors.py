"""NBA-specific processors."""

from typing import Sequence

from agentx.data._models import DataEvent
from agentx.data._processors import DataProcessor
from agentx.data.nba._events import PlayByPlayEvent, RawPlayByPlayEvent


class PlayByPlayProcessor(DataProcessor):
    """Processor that transforms raw play-by-play events to cooked events."""
    
    async def process(self, events: Sequence[DataEvent]) -> DataEvent | None:
        """Process raw play-by-play events.
        
        Args:
            events: Sequence of raw play-by-play events
            
        Returns:
            Processed play-by-play event or None
        """
        if not events:
            return None
        
        # Get the latest raw event
        raw_event = None
        for event in events:
            if isinstance(event, RawPlayByPlayEvent):
                raw_event = event
                break
        
        if not raw_event:
            return None
        
        # Transform to cooked event (could add validation, enrichment, etc.)
        return PlayByPlayEvent(
            timestamp=raw_event.timestamp,
            game_id=raw_event.game_id,
            points=raw_event.points,
            home_score=raw_event.home_score,
            away_score=raw_event.away_score,
            description=raw_event.description,
        )

