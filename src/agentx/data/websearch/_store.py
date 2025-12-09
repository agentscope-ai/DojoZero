"""Web Search data store implementation."""

from typing import Any, Sequence

from agentx.data._models import DataEvent
from agentx.data._stores import DataStore, ExternalAPI
from agentx.data.websearch._api import WebSearchAPI
from agentx.data.websearch._events import RawWebSearchEvent


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
    
    async def search(self, query: str, **search_params: Any) -> None:
        """Trigger a search and emit events.
        
        Args:
            query: Search query
            **search_params: Additional search parameters (e.g., max_results, chunks_per_source)
        """
        # For injury-related queries, request more content chunks
        # Note: Tavily limits chunks_per_source to 1-5, so we use max value
        if "injury" in query.lower() or "injured" in query.lower():
            search_params.setdefault("chunks_per_source", 5)  # Max allowed by Tavily
            search_params.setdefault("search_depth", "advanced")
            search_params.setdefault("include_raw_content", True)
        
        # Fetch from API
        data = await self._api.fetch("search", {"query": query, **search_params})
        
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
                        # Check if processor should handle this event
                        if processor.should_process(raw_event):
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

