"""DataStore: Manages external APIs, polling, and event emission."""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from collections.abc import Awaitable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

from dojozero.data._models import DataEvent, extract_game_id
from dojozero.data._processors import DataProcessor

logger = logging.getLogger(__name__)


def extract_dedup_keys_from_jsonl(
    jsonl_path: str | Path,
    game_id: str | None = None,
) -> set[str]:
    """Extract deduplication keys from a JSONL event file.

    Uses the generic get_dedup_key() method on each event to extract its
    deduplication key. This is fully extensible - adding new event types
    with get_dedup_key() implementations requires no changes here.

    Args:
        jsonl_path: Path to the JSONL event file
        game_id: Optional game ID to filter events (if None, extracts all)

    Returns:
        Set of deduplication keys from all events
    """
    # Lazy import to avoid circular dependency
    from dojozero.data import deserialize_data_event

    dedup_keys: set[str] = set()

    path = Path(jsonl_path)
    if not path.exists():
        logger.warning("JSONL file not found for dedup rebuild: %s", jsonl_path)
        return dedup_keys

    try:
        with open(path, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)

                    # Filter by game_id if provided (uses shared extraction logic)
                    if game_id:
                        evt_game_id = extract_game_id(data)
                        if evt_game_id != game_id:
                            continue

                    # Deserialize to typed event and get dedup key
                    event = deserialize_data_event(data)
                    if event:
                        key = event.get_dedup_key()
                        if key:
                            dedup_keys.add(key)

                except json.JSONDecodeError:
                    continue  # Skip malformed lines

    except Exception as e:
        logger.error("Error reading JSONL for dedup rebuild: %s", e)

    logger.info(
        "Rebuilt dedup state from JSONL: %d dedup keys",
        len(dedup_keys),
    )
    return dedup_keys


# Legacy function for backward compatibility
def extract_dedup_ids_from_jsonl(
    jsonl_path: str | Path,
    game_id: str | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """Extract deduplication IDs from a JSONL event file (legacy).

    DEPRECATED: Use extract_dedup_keys_from_jsonl() instead.

    This function maintains backward compatibility by categorizing keys
    into separate sets based on their format.

    Args:
        jsonl_path: Path to the JSONL event file
        game_id: Optional game ID to filter events (if None, extracts all)

    Returns:
        Tuple of (event_ids, play_ids, drive_ids) sets
    """
    all_keys = extract_dedup_keys_from_jsonl(jsonl_path, game_id)

    # Categorize keys by their format
    event_ids: set[str] = set()
    play_ids: set[str] = set()
    drive_ids: set[str] = set()

    for key in all_keys:
        if "_pbp_" in key:
            event_ids.add(key)
        elif "_play_" in key:
            play_ids.add(key)
        elif "_drive_" in key:
            drive_ids.add(key)
        # Other keys (e.g., pregame events) are not categorized in legacy format

    return event_ids, play_ids, drive_ids


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

    # Sport type for this store (override in subclass or set via property)
    # Used for trace context when emitting events
    sport_type: str = ""

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
        self._async_event_emitter: Callable[[DataEvent], Awaitable[None]] | None = None
        # Track DataHub reference for event subscriptions (set by connect_store)
        self._data_hub: "DataHub | None" = None

        # Stream registry: stream_id -> (processor, source_event_types)
        # Maps raw event types to processors that create cooked event streams
        self._stream_registry: dict[str, tuple[DataProcessor | None, list[str]]] = {}

        # Polling state
        self._running = False
        self._poll_task: asyncio.Task[None] | None = None  # Reference to polling task
        self._poll_gate = asyncio.Event()
        self._poll_gate.set()  # Open by default (not paused)
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
        """Set the sync event emitter callback (called by DataHub).

        Args:
            emitter: Function to call when emitting events
        """
        self._event_emitter = emitter

    def set_async_event_emitter(
        self, emitter: Callable[[DataEvent], Awaitable[None]]
    ) -> None:
        """Set the async event emitter callback (called by DataHub).

        When set, emit_event() will await this instead of the sync emitter,
        ensuring events from a single poll cycle are processed in order.

        Args:
            emitter: Async function to call when emitting events
        """
        self._async_event_emitter = emitter

    def set_poll_identifier(self, identifier: dict[str, Any]) -> None:
        """Set identifier for polling (e.g., game_id, event_id).

        Args:
            identifier: Dictionary with identifiers (e.g., {"game_id": "123"})
        """
        self._poll_identifier = identifier

    async def emit_event(self, event: DataEvent) -> None:
        """Emit an event to DataHub.

        Prefers the async emitter (sequential, preserves ordering within a poll
        cycle) over the sync emitter (fire-and-forget, no ordering guarantee).

        Args:
            event: Event to emit
        """
        if self._async_event_emitter:
            await self._async_event_emitter(event)
        elif self._event_emitter:
            self._event_emitter(event)

    async def start_polling(self) -> None:
        """Start polling the API for updates."""
        if not self._api:
            return

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())

    def pause_polling(self) -> None:
        """Pause polling without stopping the task.

        The poll loop will block at the top of the next iteration until
        ``resume_polling()`` is called.
        """
        self._poll_gate.clear()

    def resume_polling(self) -> None:
        """Resume a previously paused poll loop."""
        self._poll_gate.set()

    async def stop_polling(self) -> None:
        """Stop polling the API and close any open connections."""
        logger.info(
            "stop_polling called for store %s, _poll_task=%s, _api=%s",
            self.store_id,
            self._poll_task,
            type(self._api).__name__ if self._api else None,
        )
        self._running = False

        # Wait for the polling task to complete before closing the session
        if self._poll_task and not self._poll_task.done():
            logger.info("Waiting for poll task to complete for store %s", self.store_id)
            try:
                # Give the task a short time to finish its current iteration
                await asyncio.wait_for(self._poll_task, timeout=5.0)
                logger.info("Poll task completed normally for store %s", self.store_id)
            except asyncio.TimeoutError:
                # If it doesn't finish in time, cancel it
                logger.info(
                    "Poll task timed out, cancelling for store %s", self.store_id
                )
                self._poll_task.cancel()
                try:
                    await self._poll_task
                except asyncio.CancelledError:
                    logger.info("Poll task cancelled for store %s", self.store_id)
            except asyncio.CancelledError:
                logger.info(
                    "Poll task was already cancelled for store %s", self.store_id
                )
            self._poll_task = None
        else:
            logger.info(
                "No poll task to wait for store %s (task=%s, done=%s)",
                self.store_id,
                self._poll_task,
                self._poll_task.done() if self._poll_task else "N/A",
            )

        # Small delay to allow any pending operations to complete
        await asyncio.sleep(0.1)

        # Close the API session if it has a close method
        if self._api and hasattr(self._api, "close"):
            logger.info(
                "Closing API session for store %s (api=%s)",
                self.store_id,
                type(self._api).__name__,
            )
            try:
                close_method = getattr(self._api, "close")
                await close_method()
                logger.info(
                    "Successfully closed API session for store %s", self.store_id
                )
            except Exception as e:
                logger.warning(
                    "Error closing API session for store %s: %s", self.store_id, e
                )
        else:
            logger.info(
                "No close method on API for store %s (api=%s, has_close=%s)",
                self.store_id,
                type(self._api).__name__ if self._api else None,
                hasattr(self._api, "close") if self._api else False,
            )

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
            # Block here while paused (pause_polling / resume_polling)
            await self._poll_gate.wait()

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

    # =========================================================================
    # State Persistence (for checkpoint/resume)
    # =========================================================================

    async def save_state(self) -> dict[str, Any]:
        """Save store state for checkpointing.

        Subclasses should override this to persist their internal state
        (e.g., deduplication tracking, game lifecycle status).

        The default implementation returns minimal state. Subclasses should
        call super().save_state() and extend the returned dict.

        Returns:
            Dictionary containing serializable state
        """
        return {
            "store_id": self.store_id,
            "last_poll_times": {
                endpoint: ts.isoformat()
                for endpoint, ts in self._last_poll_times.items()
            },
            "poll_identifier": self._poll_identifier,
        }

    async def load_state(
        self,
        state: dict[str, Any],
        dedup_keys: set[str] | None = None,
    ) -> None:
        """Load store state from checkpoint.

        Subclasses should override this to restore their internal state.
        The default implementation restores basic polling state.

        Args:
            state: Dictionary containing previously saved state
            dedup_keys: Optional set of deduplication keys extracted from JSONL.
                       Subclasses filter these internally based on key format.
        """
        # Restore poll times
        last_poll_times = state.get("last_poll_times", {})
        for endpoint, ts_str in last_poll_times.items():
            try:
                self._last_poll_times[endpoint] = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                pass

        # Restore poll identifier
        poll_identifier = state.get("poll_identifier")
        if poll_identifier:
            self._poll_identifier = poll_identifier

        # Base class ignores dedup_keys; subclasses filter and use as needed
