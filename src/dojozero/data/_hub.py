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

        # Track sequence numbers per event type for trace emission
        self._event_sequences: dict[str, int] = defaultdict(int)

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

    async def receive_event(
        self,
        event: DataEvent,
        source_actor_id: str | None = None,
        sport_type: str = "",
    ) -> None:
        """Receive an event from a DataStore.

        Args:
            event: Event to receive
            source_actor_id: Actor ID of the source (store) that emitted the event
            sport_type: Sport type of the source store (e.g., "nba", "nfl")
        """
        # Cache event for late-joining subscribers
        self._cache_event(event)

        # Persist event (skip during backtest mode)
        if not self._backtest_mode:
            await self._persist_event(event)

        # Emit to trace backend if trial_id is set
        if self.trial_id and not self._backtest_mode:
            self._emit_event_span(event, source_actor_id or self.hub_id, sport_type)

        # Dispatch to subscribed agents
        await self._dispatch_event(event)

    # Class-level counters for progress logging
    _sls_emit_count: int = 0
    _sls_error_count: int = 0

    def _emit_event_span(
        self, event: DataEvent, actor_id: str, sport_type: str
    ) -> None:
        """Emit an event as a span to the trace backend.

        Args:
            event: Event to emit
            actor_id: Actor ID of the source (store) that emitted the event
            sport_type: Sport type of the source store (e.g., "nba", "nfl")
        """
        try:
            from dojozero.core._tracing import create_span_from_event, emit_span

            event_type = event.event_type
            logger.debug(
                "DataHub._emit_event_span called: event_type=%s, actor_id=%s, trial_id=%s",
                event_type,
                actor_id,
                self.trial_id,
            )

            # Increment and get sequence for this event type
            self._event_sequences[event_type] += 1
            sequence = self._event_sequences[event_type]

            # Build tags with event data
            tags: dict[str, Any] = {
                "dojozero.event.type": event_type,
                "dojozero.event.sequence": sequence,
                "dojozero.sport.type": sport_type,
            }

            # Add payload data as event.* tags
            event_dict = event.to_dict()
            for key, value in event_dict.items():
                if key in ("event_type", "timestamp"):
                    continue  # Skip metadata fields
                if isinstance(value, (dict, list)):
                    tags[f"event.{key}"] = json.dumps(value, default=str)
                else:
                    tags[f"event.{key}"] = value

            # Extract game_id as top-level tag for easier querying
            game_id = event_dict.get("game_id") or event_dict.get("event_id", "")
            if game_id:
                # Handle event_id format like "0022400608_pbp_188" -> extract game_id
                if "_" in str(game_id) and str(game_id).startswith("00"):
                    game_id = str(game_id).split("_")[0]
                tags["dojozero.game.id"] = str(game_id)

            # trial_id is guaranteed non-None here because _emit_event_span is only
            # called when self.trial_id is truthy (checked in receive_event)
            assert self.trial_id is not None
            span = create_span_from_event(
                trial_id=self.trial_id,
                actor_id=actor_id,
                operation_name=event_type,
                start_time=event.timestamp,
                extra_tags=tags,
            )
            emit_span(span)

            # Progress logging
            DataHub._sls_emit_count += 1
            if DataHub._sls_emit_count % 50 == 0:
                logger.info(
                    "DataHub SLS emit progress: %d events emitted (%d errors) "
                    "[latest: event_type=%s, trial=%s, actor=%s]",
                    DataHub._sls_emit_count,
                    DataHub._sls_error_count,
                    event_type,
                    self.trial_id,
                    actor_id,
                )

        except Exception as e:
            DataHub._sls_error_count += 1
            # Don't let trace emission failures affect event processing
            logger.warning(
                "Failed to emit event span (#%d): %s: %s (event_type=%s, actor=%s)",
                DataHub._sls_error_count,
                type(e).__name__,
                e,
                event.event_type,
                actor_id,
            )

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
        # Capture store info for use in emit_wrapper closure
        store_id = store.store_id
        store_sport_type = store.sport_type

        # Set the store's event emitter to this hub's receive_event
        # Note: event_emitter is sync callback, but we schedule async work
        def emit_wrapper(event: DataEvent) -> None:
            task = asyncio.create_task(
                self.receive_event(
                    event, source_actor_id=store_id, sport_type=store_sport_type
                )
            )
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
