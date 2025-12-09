"""DataStore: Manages external APIs, polling, and event emission."""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from agentx.data._models import DataEvent
from agentx.data._processors import DataProcessor


class ExternalAPI(ABC):
    """Abstraction layer for managing external service API connections."""
    
    def __init__(self, kwargs: dict[str, Any] | None = None):
        """Initialize external API.
        
        Args:
            api_key: Optional API key (for real implementation)
        """
        self.kwargs = kwargs

    @abstractmethod
    async def fetch(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch data from external API."""
        ...


class DataStore(ABC):
    """Manages external APIs, polling, and event emission to DataHub.
    
    DataStores are responsible for:
    - Polling external APIs
    - Transforming raw API data to DataEvents
    - Processing events through registered processors
    - Emitting cooked events to DataHub
    """
    
    def __init__(
        self,
        store_id: str,
        api: ExternalAPI | None = None,
        poll_interval_seconds: float = 5.0,
        event_emitter: Callable[[DataEvent], None] | None = None,
    ):
        """Initialize data store.
        
        Args:
            store_id: Unique identifier for this data store
            api: ExternalAPI instance
            poll_interval_seconds: How often to poll the API
            event_emitter: Callback to emit events to DataHub
        """
        self.store_id = store_id
        self._api = api
        self.poll_interval_seconds = poll_interval_seconds
        self._event_emitter = event_emitter
        
        # Stream registry: stream_id -> (processor, source_event_types)
        # Maps raw event types to processors that create cooked event streams
        self._stream_registry: dict[str, tuple[DataProcessor | None, list[str]]] = {}
        
        # Polling state
        self._running = False
        self._last_poll_time: datetime | None = None
    
    def register_stream(
        self,
        stream_id: str,
        processor: DataProcessor | None,
        source_event_types: list[str],
    ) -> None:
        """Register a stream with its processor and source event types.
        
        Args:
            stream_id: Stream identifier (e.g., "cooked_play_by_play")
            processor: Optional processor to transform raw events
            source_event_types: List of raw event types (e.g., ["raw_play_by_play"])
        """
        self._stream_registry[stream_id] = (processor, source_event_types)
    
    def list_registered_streams(self) -> list[str]:
        """List all registered stream IDs."""
        return list(self._stream_registry.keys())
    
    def set_event_emitter(self, emitter: Callable[[DataEvent], None]) -> None:
        """Set the event emitter callback (called by DataHub).
        
        Args:
            emitter: Function to call when emitting events
        """
        self._event_emitter = emitter
    
    async def emit_event(self, event: DataEvent) -> None:
        """Emit an event to DataHub.
        
        Args:
            event: Event to emit
        """
        if self._event_emitter:
            self._event_emitter(event)
    
    async def start_polling(self) -> None:
        """Start polling the API for updates."""
        if not self._api:
            return
        
        self._running = True
        asyncio.create_task(self._poll_loop())
    
    async def stop_polling(self) -> None:
        """Stop polling the API."""
        self._running = False
    
    async def _poll_loop(self) -> None:
        """Main polling loop that fetches from API and emits events."""
        while self._running:
            try:
                # Poll for updates
                raw_events = await self._poll_api()
                
                # Process raw events through registered streams
                for raw_event in raw_events:
                    # Emit raw event
                    await self.emit_event(raw_event)
                    
                    # Process through registered streams
                    for stream_id, (processor, source_types) in self._stream_registry.items():
                        if raw_event.event_type in source_types:
                            if processor:
                                # Check if processor should handle this event
                                if processor.should_process(raw_event):
                                    # Process event through processor
                                    processed = await processor.process([raw_event])
                                    if processed and isinstance(processed, DataEvent):
                                        await self.emit_event(processed)
                            else:
                                # No processor, just pass through
                                await self.emit_event(raw_event)
                
                if raw_events:
                    self._last_poll_time = max(e.timestamp for e in raw_events)
                
            except Exception as e:
                print(f"Error in poll loop for store {self.store_id}: {e}")
            
            # Wait before next poll
            await asyncio.sleep(self.poll_interval_seconds)
    
    async def _poll_api(
        self,
        event_type: str | None = None,
        identifier: dict[str, Any] | None = None,
    ) -> Sequence[DataEvent]:
        """Poll the API for new events.
        
        Args:
            event_type: Optional filter by event type
            identifier: Optional identifier dict (e.g., {"game_id": "123"})
            
        Returns:
            Sequence of new DataEvents
        """
        if not self._api:
            return []
        
        # Build API params
        params: dict[str, Any] = {}
        if event_type:
            params["event_type"] = event_type
        if identifier:
            params.update(identifier)
        if self._last_poll_time:
            params["since"] = self._last_poll_time.isoformat()
        
        # Fetch from API
        data = await self._api.fetch("events", params if params else None)
        
        # Convert to DataEvents (implementation-specific)
        events = self._parse_api_response(data)
        
        return events
    
    @abstractmethod
    def _parse_api_response(self, data: dict[str, Any]) -> Sequence[DataEvent]:
        """Parse API response into DataEvents.
        
        Args:
            data: Raw API response data
            
        Returns:
            Sequence of DataEvents
        """
        ...

