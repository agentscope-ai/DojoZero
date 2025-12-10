"""Data processors for transforming events."""

import asyncio
from abc import ABC, abstractmethod
from typing import Sequence

from agentx.data._models import DataEvent, DataFact


class DataProcessor(ABC):
    """Base class for processors that transform events.
    
    Processors can transform raw events into cooked events or facts.
    They are registered in DataStores for stream processing.
    """
    
    def should_process(self, event: DataEvent) -> bool:
        """Check if this processor should handle the given event.
        
        This method allows processors to filter events before processing,
        enabling efficient routing and avoiding unnecessary processing.
        
        Processors can override this to implement custom filtering logic.
        If the event has an 'intent' attribute, it will be checked first
        against the processor's intended_intent (if set).
        
        Args:
            event: Event to check
            
        Returns:
            True if processor should process this event, False otherwise
        """
        # Check intent first if both event and processor have intent
        event_intent = getattr(event, "intent", None)
        processor_intent = getattr(self, "intended_intent", None)
        
        if event_intent is not None and processor_intent is not None:
            # Intent-based routing: normalize to string for comparison
            # Handles both enum and string values
            from enum import Enum
            event_intent_str = event_intent.value if isinstance(event_intent, Enum) else str(event_intent)
            processor_intent_str = processor_intent.value if isinstance(processor_intent, Enum) else str(processor_intent)
            return event_intent_str == processor_intent_str
        
        # Fallback to default behavior (process all)
        return True  # Default: process all events
    
    @abstractmethod
    async def process(self, event: DataEvent) -> DataEvent | DataFact | None:
        """Process a single event and return transformed event or fact.
        
        Args:
            event: Input event to process
            
        Returns:
            Transformed event, fact, or None
        """
        ...
    
    async def process_batched(self, events: Sequence[DataEvent]) -> Sequence[DataEvent | DataFact]:
        """Process multiple events in batch (optional override).
        
        Default implementation processes all events in parallel and returns
        all successfully processed events. Override this method if you need
        custom batching logic (e.g., aggregation, filtering, reduction).
        
        Args:
            events: Sequence of input events
            
        Returns:
            Sequence of successfully transformed events/facts (may be empty)
        """
        if not events:
            return []
        
        # Process all events in parallel
        results = await asyncio.gather(*[self.process(event) for event in events], return_exceptions=True)
        
        # Filter out None and exceptions, return all successful results
        processed: list[DataEvent | DataFact] = []
        for result in results:
            if result is not None and not isinstance(result, BaseException):
                processed.append(result)  # type: ignore[arg-type]
        
        return processed


class CompositeProcessor(DataProcessor):
    """Processor that chains multiple processors together."""
    
    def __init__(self, processors: Sequence[DataProcessor]):
        """Initialize composite processor.
        
        Args:
            processors: Sequence of processors to chain
        """
        self.processors = processors
    
    def should_process(self, event: DataEvent) -> bool:
        """Check if any processor in the chain should process this event."""
        return any(processor.should_process(event) for processor in self.processors)
    
    async def process(self, event: DataEvent) -> DataEvent | DataFact | None:
        """Process event through all processors in sequence."""
        result = event
        for processor in self.processors:
            if processor.should_process(result):
                processed = await processor.process(result)
                if processed is None:
                    return None
                if isinstance(processed, DataEvent):
                    result = processed
                elif isinstance(processed, DataFact):
                    return processed
            else:
                # Processor doesn't handle this event type, skip
                continue
        return result

