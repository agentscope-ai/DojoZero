"""Web Search data store implementation."""

from typing import Any, Sequence

from agentx.data._models import DataEvent
from agentx.data._stores import DataStore, ExternalAPI
from agentx.data.websearch._api import WebSearchAPI
from agentx.data.websearch._events import RawWebSearchEvent, WebSearchIntent


class WebSearchStore(DataStore):
    """Web Search data store for querying search API and emitting events.

    Note: This store does not poll automatically. It only emits events when
    search() is called explicitly (e.g., by a stream initializer).
    """

    def __init__(
        self,
        store_id: str = "web_search_store",
        api: ExternalAPI | None = None,
        event_emitter=None,
    ):
        """Initialize Web Search store."""
        super().__init__(
            store_id, api=api or WebSearchAPI(), event_emitter=event_emitter
        )

    async def start_polling(self) -> None:
        """Override to prevent automatic polling.

        WebSearchStore should only be triggered by explicit search() calls,
        not by polling. This prevents errors when DataHub.start() is called.
        """
        # Do nothing - WebSearchStore doesn't poll
        pass

    async def search(
        self,
        query: str,
        intent: WebSearchIntent | str | None = None,
        **search_params: Any,
    ) -> None:
        """Trigger a search and emit events."""
        # Use optimal settings for all queries to get complete content
        search_params.setdefault("search_depth", "advanced")
        search_params.setdefault("max_results", 5)
        search_params.setdefault("include_raw_content", True)

        # Fetch from API
        assert self._api is not None, "API must be initialized"
        data = await self._api.fetch("search", {"query": query, **search_params})

        # Parse raw events with intent
        raw_events = self._parse_api_response(data, intent=intent)

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
                            processed = await processor.process(raw_event)
                            if processed and isinstance(processed, DataEvent):
                                await self.emit_event(processed)
                    else:
                        # No processor, just pass through
                        await self.emit_event(raw_event)

    def _parse_api_response(
        self, data: dict[str, Any], intent: str | None = None
    ) -> Sequence[DataEvent]:
        """Parse Web Search API response into DataEvents.

        Args:
            data: API response data
            intent: Optional query intent to attach to the event
        """
        from datetime import datetime, timezone

        query = data.get("query", "")
        results = data.get("results", [])

        return [
            RawWebSearchEvent(
                timestamp=datetime.now(timezone.utc),
                query=query,
                results=results,
                intent=intent,
            )
        ]
