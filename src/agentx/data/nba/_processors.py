"""NBA-specific processors."""

from typing import cast

from agentx.data._models import DataEvent
from agentx.data._processors import DataProcessor
from agentx.data.nba._events import PlayByPlayEvent, RawPlayByPlayEvent


class PlayByPlayProcessor(DataProcessor):
    """Processor that transforms raw play-by-play events to cooked events."""
    
    def should_process(self, event: DataEvent) -> bool:
        """Check if this processor should handle the event.
        
        Only processes raw play-by-play events.
        
        Args:
            event: Event to check
            
        Returns:
            True if event is raw play-by-play, False otherwise
        """
        return event.event_type == "raw_play_by_play"
    
    async def process(self, event: DataEvent) -> DataEvent | None:
        """Process raw play-by-play event.
        
        Args:
            event: Raw play-by-play event
            
        Returns:
            Processed play-by-play event or None
        """
        if event.event_type != "raw_play_by_play":
            return None
        
        raw_event = cast(RawPlayByPlayEvent, event)  # type: ignore[arg-type]
        
        # Transform to cooked event (could add validation, enrichment, etc.)
        return PlayByPlayEvent(  # type: ignore[call-arg]
            timestamp=raw_event.timestamp,
            game_id=raw_event.game_id,
            points=raw_event.points,
            home_score=raw_event.home_score,
            away_score=raw_event.away_score,
            description=raw_event.description,
        )

