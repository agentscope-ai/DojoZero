"""DataHub: Central event bus for persistence, merging, and delivery."""

import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from dojozero.data._models import DataEvent

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from dojozero.data._stores import DataStore


class DataHub:
    """Central event bus for persistence, merging, and delivery.

    Responsibilities:
    - Receive events from all DataStores
    - Persist events to file (timestamped, typed)
    - Manage agent subscriptions
    - Dispatch events to subscribed agents
    - Emit events to trace backend (OTel/SLS)
    - Support backtest mode
    """

    def __init__(
        self,
        hub_id: str,
        persistence_file: Path | str,
        trial_id: str | None = None,
    ):
        """Initialize DataHub.

        Args:
            hub_id: Unique identifier for this hub
            persistence_file: Path to file for event persistence (required)
            trial_id: Trial identifier for trace emission (optional)
        """
        self.hub_id = hub_id
        self.trial_id = trial_id
        self.persistence_file = Path(persistence_file)

        logger.info(
            "DataHub initialized: hub_id=%s, trial_id=%s, persistence_file=%s",
            hub_id,
            trial_id,
            self.persistence_file,
        )

        # Ensure persistence directory exists
        self.persistence_file.parent.mkdir(parents=True, exist_ok=True)

        # Agent subscriptions: agent_id -> list of stream_ids or event_types
        self._agent_subscriptions: dict[str, set[str]] = defaultdict(set)

        # Event handlers: event_type -> list of callbacks
        self._event_handlers: dict[str, list[Callable[[DataEvent], None]]] = (
            defaultdict(list)
        )

        # Backtest mode
        self._backtest_mode = False
        self._backtest_events: list[DataEvent] = []
        self._backtest_index = 0

        # Track connected stores for lifecycle management
        self._connected_stores: list["DataStore"] = []

        # Cache recent events for late-joining subscribers
        # Key: event_type, Value: list of recent events (newest first)
        self._recent_events: dict[str, list[DataEvent]] = defaultdict(list)
        self._max_recent_events_per_type = 100  # Keep last 100 events per type

    def subscribe_agent(
        self,
        agent_id: str,
        stream_ids: list[str] | None = None,
        event_types: list[str] | None = None,
        callback: Callable[[DataEvent], None] | None = None,
    ) -> None:
        """Subscribe an agent to receive events.

        Args:
            agent_id: Agent identifier
            stream_ids: List of stream IDs to subscribe to
            event_types: List of event types to subscribe to
            callback: Callback function to receive events
        """
        if stream_ids:
            self._agent_subscriptions[agent_id].update(stream_ids)
        if event_types:
            self._agent_subscriptions[agent_id].update(event_types)
        if callback:
            # Register callback for all subscribed types
            for event_type in event_types or []:
                self._event_handlers[event_type].append(callback)

    def unsubscribe_agent(self, agent_id: str) -> None:
        """Unsubscribe an agent.

        Args:
            agent_id: Agent identifier
        """
        if agent_id in self._agent_subscriptions:
            del self._agent_subscriptions[agent_id]

    async def receive_event(self, event: DataEvent) -> None:
        """Receive an event from a DataStore.

        Args:
            event: Event to receive
        """
        # Cache event for late-joining subscribers
        self._cache_event(event)

        # Persist event (skip during backtest mode)
        if not self._backtest_mode:
            await self._persist_event(event)

        # Note: Event span emission is handled by DataStream._emit_event_span()
        # when the stream publishes the event. We don't emit here to avoid duplicates.

        # Dispatch to subscribed agents
        await self._dispatch_event(event)

    def _cache_event(self, event: DataEvent) -> None:
        """Cache event for late-joining subscribers."""
        event_type = event.event_type
        events_list = self._recent_events[event_type]
        events_list.insert(0, event)  # Newest first
        # Trim to max size
        if len(events_list) > self._max_recent_events_per_type:
            self._recent_events[event_type] = events_list[
                : self._max_recent_events_per_type
            ]

    def get_recent_events(
        self,
        event_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[DataEvent]:
        """Get recent events from the cache.

        Args:
            event_types: Filter by event types (None = all types)
            limit: Maximum number of events to return

        Returns:
            List of recent events (newest first)
        """
        if event_types is None:
            # Get all recent events across all types
            all_events: list[DataEvent] = []
            for events_list in self._recent_events.values():
                all_events.extend(events_list)
            # Sort by timestamp (newest first) and limit
            all_events.sort(key=lambda e: e.timestamp, reverse=True)
            return all_events[:limit]
        else:
            # Get events for specific types
            result: list[DataEvent] = []
            for event_type in event_types:
                if event_type in self._recent_events:
                    result.extend(self._recent_events[event_type])
            # Sort by timestamp (newest first) and limit
            result.sort(key=lambda e: e.timestamp, reverse=True)
            return result[:limit]

    async def _persist_event(self, event: DataEvent) -> None:
        """Persist event to file.

        Args:
            event: Event to persist
        """
        event_dict = event.to_dict()
        line = json.dumps(event_dict) + "\n"
        # Use thread pool to avoid blocking the event loop
        await asyncio.to_thread(self._write_to_file, line)

    def _write_to_file(self, line: str) -> None:
        """Write a line to the persistence file (sync, runs in thread pool)."""
        from pathlib import Path

        # Ensure parent directory exists
        Path(self.persistence_file).parent.mkdir(parents=True, exist_ok=True)
        with open(self.persistence_file, "a") as f:
            f.write(line)

    async def _dispatch_event(self, event: DataEvent) -> None:
        """Dispatch event to subscribed agents.

        Args:
            event: Event to dispatch
        """
        event_type = event.event_type

        # Dispatch to handlers for this event type
        if event_type in self._event_handlers:
            for handler in self._event_handlers[event_type]:
                try:
                    handler(event)
                except Exception as e:
                    logger.error("Error in event handler for %s: %s", event_type, e)

        # Dispatch to agents subscribed to this event type
        for agent_id, subscriptions in self._agent_subscriptions.items():
            if event_type in subscriptions:
                # Agent is subscribed to this event type
                # In a real implementation, this would call agent's receive method
                pass

    def connect_store(self, store: "DataStore") -> None:
        """Connect a DataStore to this hub.

        Args:
            store: DataStore instance
        """

        # Set the store's event emitter to this hub's receive_event
        # Note: event_emitter is sync callback, but we schedule async work
        def emit_wrapper(event: DataEvent) -> None:
            task = asyncio.create_task(self.receive_event(event))
            task.add_done_callback(self._handle_task_exception)

        store.set_event_emitter(emit_wrapper)

        # Store DataHub reference in store so it can subscribe to events if needed
        if hasattr(store, "_data_hub"):
            store._data_hub = self

        # Track connected store for lifecycle management
        if store not in self._connected_stores:
            self._connected_stores.append(store)

    def _handle_task_exception(self, task: asyncio.Task[None]) -> None:
        """Handle exceptions from background tasks.

        Args:
            task: Completed task to check for exceptions
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "Background task failed in DataHub: %s",
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    async def start_backtest(self, backtest_file: Path | str) -> None:
        """Start backtest mode from a file.

        Args:
            backtest_file: Path to backtest file
        """
        self._backtest_mode = True
        self._backtest_events = []
        self._backtest_index = 0

        backtest_path = Path(backtest_file)
        if not backtest_path.exists():
            raise FileNotFoundError(f"Backtest file not found: {backtest_path}")

        # Load events from file
        with open(backtest_path, "r") as f:
            for line in f:
                if line.strip():
                    event_dict = json.loads(line)
                    # Reconstruct event from dict
                    event = self._reconstruct_event(event_dict)
                    if event:
                        self._backtest_events.append(event)

        # Sort events by timestamp
        self._backtest_events.sort(key=lambda e: e.timestamp)

    # Backward compatibility alias (deprecated)
    async def start_replay(self, replay_file: Path | str) -> None:
        """Deprecated: Use start_backtest instead."""
        await self.start_backtest(replay_file)

    def _reconstruct_event(self, event_dict: dict[str, Any]) -> DataEvent | None:
        """Reconstruct event from dictionary.

        Args:
            event_dict: Event dictionary

        Returns:
            Reconstructed DataEvent or None
        """
        event_type = event_dict.get("event_type")
        if not event_type:
            return None

        try:
            # Use DataEventFactory to deserialize
            from dojozero.data._models import DataEventFactory

            return DataEventFactory.from_dict(event_dict)
        except Exception as e:
            logger.warning("Error reconstructing event: %s", e)
            return None

    async def backtest_next(self) -> DataEvent | None:
        """Process next event in backtest.

        Returns:
            Next event or None if backtest is complete
        """
        if self._backtest_index >= len(self._backtest_events):
            return None

        event = self._backtest_events[self._backtest_index]
        self._backtest_index += 1

        # Dispatch event as if it just arrived
        await self._dispatch_event(event)

        return event

    # Backward compatibility alias (deprecated)
    async def replay_next(self) -> DataEvent | None:
        """Deprecated: Use backtest_next instead."""
        return await self.backtest_next()

    async def backtest_all(self) -> None:
        """Run backtest for all events in order."""
        for event in self._backtest_events:
            await self._dispatch_event(event)

    # Backward compatibility alias (deprecated)
    async def replay_all(self) -> None:
        """Deprecated: Use backtest_all instead."""
        await self.backtest_all()

    def stop_backtest(self) -> None:
        """Stop backtest mode."""
        self._backtest_mode = False
        self._backtest_events = []
        self._backtest_index = 0

    # Backward compatibility alias (deprecated)
    def stop_replay(self) -> None:
        """Deprecated: Use stop_backtest instead."""
        self.stop_backtest()

    async def start(self) -> None:
        """Start all connected stores (begin polling).

        This should be called after all stores are connected and configured
        (e.g., after poll identifiers are set).
        """
        for store in self._connected_stores:
            await store.start_polling()

    async def stop(self) -> None:
        """Stop all connected stores (stop polling)."""
        for store in self._connected_stores:
            await store.stop_polling()
