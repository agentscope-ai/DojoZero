"""Web Search data store implementation."""

from typing import Any, Sequence

from dojozero.data._models import DataEvent, WebSearchInsightEvent
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data.websearch._api import WebSearchAPI


class WebSearchStore(DataStore):
    """Web Search data store for querying search API and emitting events.

    Note: This store does not poll automatically. It only emits events when
    search() is called explicitly.
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
        pass

    async def search(
        self,
        query: str,
        **search_params: Any,
    ) -> None:
        """Trigger a search and emit raw events."""
        search_params.setdefault("search_depth", "advanced")
        search_params.setdefault("max_results", 5)
        search_params.setdefault("include_raw_content", True)

        assert self._api is not None, "API must be initialized"
        data = await self._api.fetch("search", {"query": query, **search_params})

        raw_events = self._parse_api_response(data)

        for raw_event in raw_events:
            await self.emit_event(raw_event)

    def _parse_api_response(
        self,
        data: dict[str, Any],
    ) -> Sequence[DataEvent]:
        """Parse Web Search API response into DataEvents."""
        from datetime import datetime, timezone

        query = data.get("query", "")
        results = data.get("results", [])

        return [
            WebSearchInsightEvent(
                timestamp=datetime.now(timezone.utc),
                query=query,
                raw_results=results,
                source="websearch",
            )
        ]
