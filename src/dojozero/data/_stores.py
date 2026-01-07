"""DataStore: Manages external APIs, polling, and event emission."""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Sequence

from dojozero.data._models import DataEvent
from dojozero.data._processors import DataProcessor

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from dojozero.data._hub import DataHub


class ExternalAPI(ABC):
    """Abstraction layer for managing external service API connections."""

    def __init__(self, kwargs: dict[str, Any] | None = None):
        """Initialize external API.

        Args:
            api_key: Optional API key (for real implementation)
        """
        self.kwargs = kwargs

    @abstractmethod
    async def fetch(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
        poll_intervals: dict[str, float] | None = None,
        event_emitter: Callable[[DataEvent], None] | None = None,
    ):
        """Initialize data store.

        Args:
            store_id: Unique identifier for this data store
            api: ExternalAPI instance
            poll_intervals: Per-endpoint polling intervals (e.g., {"scoreboard": 5.0, "play_by_play": 2.0})
                           If not provided, defaults to empty dict (no polling)
            event_emitter: Callback to emit events to DataHub
        """
        self.store_id = store_id
        self._api = api
        self.poll_intervals = poll_intervals or {}
        # Calculate base interval from poll_intervals (minimum value, or 5.0 if empty)
        self.poll_interval_seconds = (
            min(self.poll_intervals.values()) if self.poll_intervals else 5.0
        )
        self._event_emitter = event_emitter
        # Track DataHub reference for event subscriptions (set by connect_store)
        self._data_hub: "DataHub | None" = None

        # Stream registry: stream_id -> (processor, source_event_types)
        # Maps raw event types to processors that create cooked event streams
        self._stream_registry: dict[str, tuple[DataProcessor | None, list[str]]] = {}

        # Polling state
        self._running = False
        self._last_poll_time: datetime | None = None
        self._last_poll_times: dict[str, datetime] = {}  # Per-endpoint last poll times
        self._poll_identifier: dict[
            str, Any
        ] = {}  # Identifier for polling (e.g., {"game_id": "123"})

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

    def set_poll_identifier(self, identifier: dict[str, Any]) -> None:
        """Set identifier for polling (e.g., game_id, event_id).

        Args:
            identifier: Dictionary with identifiers (e.g., {"game_id": "123"})
        """
        self._poll_identifier = identifier

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

    def _get_poll_interval(self, endpoint: str | None = None) -> float:
        """Get polling interval for a specific endpoint.

        Args:
            endpoint: Endpoint name (e.g., "scoreboard", "play_by_play")
                    If None, returns the base interval

        Returns:
            Polling interval in seconds
        """
        if endpoint and endpoint in self.poll_intervals:
            return self.poll_intervals[endpoint]
        # Return base interval (calculated from poll_intervals in __init__)
        return self.poll_interval_seconds

    def _should_poll_endpoint(self, endpoint: str) -> bool:
        """Check if enough time has passed to poll an endpoint.

        Args:
            endpoint: Endpoint name (e.g., "scoreboard", "play_by_play")

        Returns:
            True if endpoint should be polled, False otherwise
        """
        if endpoint not in self._last_poll_times:
            return True  # Never polled, should poll now

        interval = self._get_poll_interval(endpoint)
        last_poll = self._last_poll_times[endpoint]
        elapsed = (datetime.now(timezone.utc) - last_poll).total_seconds()
        return elapsed >= interval

    def _record_poll_time(self, endpoint: str) -> None:
        """Record the current time as the last poll time for an endpoint.

        Args:
            endpoint: Endpoint name
        """
        self._last_poll_times[endpoint] = datetime.now(timezone.utc)

    def update_poll_interval(self, endpoint: str, interval: float) -> None:
        """Update polling interval for a specific endpoint dynamically.

        This allows stores to adjust polling frequency based on game status
        or other conditions (e.g., pre-game vs in-game).

        Args:
            endpoint: Endpoint name (e.g., "odds", "boxscore")
            interval: New polling interval in seconds
        """
        self.poll_intervals[endpoint] = interval
        # Recalculate base interval (minimum of all intervals)
        if self.poll_intervals:
            self.poll_interval_seconds = min(self.poll_intervals.values())
        else:
            self.poll_interval_seconds = 5.0

    async def _poll_loop(self) -> None:
        """Main polling loop that fetches from API and emits events.

        The loop interval is read dynamically on each iteration to support
        runtime interval updates (e.g., switching from pre-game to in-game polling).
        """
        while self._running:
            try:
                # Poll for updates (pass poll identifier)
                raw_events = await self._poll_api(identifier=self._poll_identifier)

                # Process raw events through registered streams
                for raw_event in raw_events:
                    # Emit raw event
                    await self.emit_event(raw_event)

                    # Process through registered streams
                    for stream_id, (
                        processor,
                        source_types,
                    ) in self._stream_registry.items():
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

                if raw_events:
                    self._last_poll_time = max(e.timestamp for e in raw_events)

            except Exception as e:
                logger.error("Error in poll loop for store %s: %s", self.store_id, e)

            # Wait before next poll - read interval dynamically to support runtime updates
            # Recalculate minimum interval on each iteration (in case it was updated)
            if self.poll_intervals:
                loop_interval = min(self.poll_intervals.values())
            else:
                loop_interval = self.poll_interval_seconds
            await asyncio.sleep(loop_interval)

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
