"""Web Search data store implementation."""

from typing import Any, Sequence

from agentx.data._models import DataEvent
from agentx.data._stores import DataStore, ExternalAPI
from agentx.data.websearch._api import WebSearchAPI
from agentx.data.websearch._events import RawWebSearchEvent
from agentx.data.websearch._processors import WebSearchProcessor


class WebSearchStore(DataStore):
    """Web Search data store for polling search API and emitting events."""
    
    def __init__(
        self,
        store_id: str = "web_search_store",
        api: ExternalAPI | None = None,
        poll_interval_seconds: float = 5.0,
        event_emitter=None,
    ):
        """Initialize Web Search store."""
        super().__init__(store_id, api or WebSearchAPI(), poll_interval_seconds, event_emitter)
        
        # Register stream: raw_web_search -> processor -> web_search
        self.register_stream(
            "web_search",
            WebSearchProcessor(),
            ["raw_web_search"],
        )
    
    async def search(self, query: str) -> None:
        """Trigger a search and emit events.
        
        Args:
            query: Search query
        """
        # Fetch from API
        data = await self._api.fetch("search", {"query": query})
        
        # Parse raw events
        raw_events = self._parse_api_response(data)
        
        # Process through registered streams (same logic as poll loop)
        for raw_event in raw_events:
            # Emit raw event
            await self.emit_event(raw_event)
            
            # Process through registered streams
            for stream_id, (processor, source_types) in self._stream_registry.items():
                if raw_event.event_type in source_types:
                    if processor:
                        # Process event through processor
                        processed = await processor.process([raw_event])
                        if processed and isinstance(processed, DataEvent):
                            await self.emit_event(processed)
                    else:
                        # No processor, just pass through
                        await self.emit_event(raw_event)
    
    def _parse_api_response(self, data: dict[str, Any]) -> Sequence[DataEvent]:
        """Parse Web Search API response into DataEvents."""
        from datetime import datetime, timezone
        
        query = data.get("query", "")
        results = data.get("results", [])
        
        return [
            RawWebSearchEvent(
                timestamp=datetime.now(timezone.utc),
                query=query,
                results=results,
            )
        ]

