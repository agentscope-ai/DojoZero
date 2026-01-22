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
    - Support replay mode
    """

    def __init__(
        self,
        hub_id: str = "data_hub",
        persistence_file: Path | str | None = None,
        enable_persistence: bool = True,
    ):
        """Initialize DataHub.

        Args:
            hub_id: Unique identifier for this hub
            persistence_file: Path to file for event persistence
            enable_persistence: Whether to persist events to file
        """
        self.hub_id = hub_id
        self.enable_persistence = enable_persistence

        if persistence_file:
            self.persistence_file = Path(persistence_file)
        else:
            self.persistence_file = Path("data/events.jsonl")

        # Ensure persistence directory exists
        if self.enable_persistence:
            self.persistence_file.parent.mkdir(parents=True, exist_ok=True)

        # Agent subscriptions: agent_id -> list of stream_ids or event_types
        self._agent_subscriptions: dict[str, set[str]] = defaultdict(set)

        # Event handlers: event_type -> list of callbacks
        self._event_handlers: dict[str, list[Callable[[DataEvent], None]]] = (
            defaultdict(list)
        )

        # Replay mode
        self._replay_mode = False
        self._replay_events: list[DataEvent] = []
        self._replay_index = 0

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

        # Persist event if enabled
        if self.enable_persistence and not self._replay_mode:
            await self._persist_event(event)

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

    async def start_replay(self, replay_file: Path | str) -> None:
        """Start replay mode from a file.

        Args:
            replay_file: Path to replay file
        """
        self._replay_mode = True
        self._replay_events = []
        self._replay_index = 0

        replay_path = Path(replay_file)
        if not replay_path.exists():
            raise FileNotFoundError(f"Replay file not found: {replay_path}")

        # Load events from file
        with open(replay_path, "r") as f:
            for line in f:
                if line.strip():
                    event_dict = json.loads(line)
                    # Reconstruct event from dict
                    event = self._reconstruct_event(event_dict)
                    if event:
                        self._replay_events.append(event)

        # Sort events by timestamp
        self._replay_events.sort(key=lambda e: e.timestamp)

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

    async def replay_next(self) -> DataEvent | None:
        """Replay next event.

        Returns:
            Next event or None if replay is complete
        """
        if self._replay_index >= len(self._replay_events):
            return None

        event = self._replay_events[self._replay_index]
        self._replay_index += 1

        # Dispatch event as if it just arrived
        await self._dispatch_event(event)

        return event

    async def replay_all(self) -> None:
        """Replay all events in order."""
        for event in self._replay_events:
            await self._dispatch_event(event)

    def stop_replay(self) -> None:
        """Stop replay mode."""
        self._replay_mode = False
        self._replay_events = []
        self._replay_index = 0

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
