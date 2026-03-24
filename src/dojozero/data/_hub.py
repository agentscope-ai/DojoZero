"""DataHub: Central event bus for persistence, merging, and delivery."""

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from dojozero.data._models import (
    DataEvent,
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    OddsUpdateEvent,
    PreGameInsightEvent,
    extract_game_id,
)
from dojozero.data._subscriptions import (
    SubscriptionManager,
)

logger = logging.getLogger(__name__)


class _GamePhase(Enum):
    """Per-game lifecycle phase for event dispatch ordering.

    PENDING  → waiting for GameInitializeEvent (all events buffered)
    PREGAME  → initialized, pre-game insights and odds flow; game events buffered
    LIVE     → game started, everything flows
    """

    PENDING = "pending"
    PREGAME = "pregame"
    LIVE = "live"


@dataclass(slots=True)
class _EventEnvelope:
    """Bundles a DataEvent with its trace/persistence context.

    Keeps trace metadata attached to the event through the gate and buffer
    so persistence and trace emission happen at delivery time, not arrival.
    """

    event: DataEvent
    source_actor_id: str = ""
    sport_type: str = ""
    game_id: str = ""
    game_date: str = ""


if TYPE_CHECKING:
    from dojozero.data._stores import DataStore


class DataHub:
    """Central event bus for persistence, merging, and delivery.

    Responsibilities:
    - Receive events from all DataStores
    - Persist events to file (timestamped, typed)
    - Manage agent subscriptions
    - Dispatch events to subscribed agents
    - Emit events to trace backend (OTel/Jaeger)
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

        # Subscription manager handles all subscription logic
        self._subscription_manager = SubscriptionManager(
            max_recent_events_per_type=100,
        )

        # Backward compatibility: maintain the original _agent_subscriptions structure
        # Maps agent_id -> set of event_types (for code that reads this directly)
        self._agent_subscriptions: dict[str, set[str]] = defaultdict(set)

        # Point to subscription manager's event handlers for direct access
        self._event_handlers = self._subscription_manager._event_handlers

        # Backtest mode
        self._backtest_mode = False
        self._backtest_events: list[DataEvent] = []
        self._backtest_index = 0
        self._emit_backtest_traces = False
        self._backtest_time_offset: timedelta | None = None

        # Track connected stores for lifecycle management
        self._connected_stores: list["DataStore"] = []

        # Recent events cache - delegate to subscription manager
        self._recent_events = self._subscription_manager._recent_events

        # Track sequence numbers per event type for trace emission
        self._event_sequences: dict[str, int] = defaultdict(int)

        # Event lifecycle ordering gate (per game_id)
        self._game_phases: dict[str, _GamePhase] = {}
        self._pending_dispatch: dict[str, list[_EventEnvelope]] = defaultdict(list)
        self._max_pending_per_game: int = 200

        # Pregame callbacks: invoked after GameInitializeEvent with stores paused
        self._on_game_initialized_callbacks: list[Callable[[str], Awaitable[None]]] = []

        # Deduplication: track event keys to prevent duplicate events on resume
        self._dedup_keys: set[str] = set()

    def load_dedup_keys(self, keys: set[str]) -> None:
        """Load deduplication keys from JSONL on resume.

        Also restores game phases based on which lifecycle events have been seen.
        This ensures the gate doesn't block events after resume.

        Args:
            keys: Set of dedup keys extracted from JSONL file
        """
        self._dedup_keys = keys.copy()
        logger.info("DataHub loaded %d dedup keys for resume", len(self._dedup_keys))

        # Restore game phases from dedup keys
        # Key format: "{game_id}_{event_type}"
        for key in keys:
            if "_event.game_start" in key:
                # Game has started - set to LIVE
                game_id = key.replace("_event.game_start", "")
                self._game_phases[game_id] = _GamePhase.LIVE
                logger.info("Restored game phase LIVE for game_id=%s", game_id)
            elif "_event.game_initialize" in key:
                # Game initialized but not started - set to PREGAME (unless already LIVE)
                game_id = key.replace("_event.game_initialize", "")
                if game_id not in self._game_phases:
                    self._game_phases[game_id] = _GamePhase.PREGAME
                    logger.info("Restored game phase PREGAME for game_id=%s", game_id)

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
            stream_ids: List of stream IDs to subscribe to (legacy, kept for compatibility)
            event_types: List of event types to subscribe to
            callback: Callback function to receive events
        """
        # Maintain backward-compatible _agent_subscriptions structure
        if stream_ids:
            self._agent_subscriptions[agent_id].update(stream_ids)
        if event_types:
            self._agent_subscriptions[agent_id].update(event_types)

        # Delegate callback registration to subscription manager
        self._subscription_manager.subscribe_agent_legacy(
            agent_id=agent_id,
            stream_ids=stream_ids,
            event_types=event_types,
            callback=callback,
        )

    def unsubscribe_agent(self, agent_id: str) -> None:
        """Unsubscribe an agent and clean up all callbacks.

        Note: This now properly cleans up callbacks, fixing the original bug
        where callbacks were not removed on unsubscribe.

        Args:
            agent_id: Agent identifier
        """
        # Clean up backward-compatible _agent_subscriptions
        if agent_id in self._agent_subscriptions:
            del self._agent_subscriptions[agent_id]

        # Use asyncio to run the async cleanup for subscription manager
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._subscription_manager.unsubscribe_all(agent_id))
        except RuntimeError:
            # No event loop running - use sync cleanup
            # This handles cases where unsubscribe is called outside async context
            asyncio.run(self._subscription_manager.unsubscribe_all(agent_id))

    async def receive_event(
        self,
        event: DataEvent,
        source_actor_id: str | None = None,
        sport_type: str = "",
        game_id: str = "",
        game_date: str = "",
    ) -> None:
        """Receive an event from a DataStore.

        Args:
            event: Event to receive
            source_actor_id: Actor ID of the source (store) that emitted the event
            sport_type: Sport type of the source store (e.g., "nba", "nfl")
            game_id: Game ID from the source store's poll_identifier (authoritative)
            game_date: Game date from the source store's poll_identifier (YYYY-MM-DD)
        """
        # Check deduplication: skip events we've already processed
        dedup_key = event.get_dedup_key()
        if dedup_key and dedup_key in self._dedup_keys:
            logger.debug("Skipping duplicate event: %s", dedup_key)
            return

        # Cache event for late-joining subscribers (arrival order, always)
        self._cache_event(event)

        # Bundle event with trace context for gated delivery
        envelope = _EventEnvelope(
            event=event,
            source_actor_id=source_actor_id or self.hub_id,
            sport_type=sport_type,
            game_id=game_id,
            game_date=game_date,
        )

        # Gate controls persistence, trace emission, and dispatch ordering
        await self._gated_dispatch(envelope)

        # Track dedup key after successful processing
        if dedup_key:
            self._dedup_keys.add(dedup_key)

    # Class-level counters for progress logging
    _trace_emit_count: int = 0
    _trace_error_count: int = 0

    def _emit_event_span(
        self,
        event: DataEvent,
        actor_id: str,
        sport_type: str,
        game_id: str = "",
        game_date: str = "",
        timestamp_override: datetime | None = None,
    ) -> None:
        """Emit an event as a span to the trace backend.

        Args:
            event: Event to emit
            actor_id: Actor ID of the source (store) that emitted the event
            sport_type: Sport type of the source store (e.g., "nba", "nfl")
            game_id: Game ID from the source store (authoritative, fallback to event)
            game_date: Game date from the source store (YYYY-MM-DD, authoritative)
            timestamp_override: If set, use this instead of event.timestamp for the span
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
                "sequence": sequence,
                "sport.type": sport_type,
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
            # Use store's game_id (authoritative), fall back to event payload
            resolved_game_id = game_id or extract_game_id(event_dict)
            if resolved_game_id:
                tags["game.id"] = resolved_game_id

            # Extract game_date as top-level tag (YYYY-MM-DD format)
            # Use store's game_date (authoritative), fall back to event payload
            resolved_game_date = game_date  # From store's poll_identifier
            if not resolved_game_date:
                # Try game_time field (datetime) - used by NBA/NFL events
                if "game_time" in event_dict:
                    game_time_val = event_dict["game_time"]
                    if isinstance(game_time_val, datetime):
                        resolved_game_date = game_time_val.strftime("%Y-%m-%d")
                    elif isinstance(game_time_val, str) and game_time_val:
                        # ISO format string - extract date portion
                        resolved_game_date = (
                            game_time_val[:10] if len(game_time_val) >= 10 else None
                        )
            if not resolved_game_date:
                # Fallback to game_time_utc (string) - used by some NBA events
                if "game_time_utc" in event_dict:
                    game_time_utc = event_dict["game_time_utc"]
                    if isinstance(game_time_utc, str) and len(game_time_utc) >= 10:
                        resolved_game_date = game_time_utc[:10]  # YYYY-MM-DD
            if resolved_game_date:
                tags["game.date"] = resolved_game_date

            # trial_id is guaranteed non-None here because _emit_event_span is only
            # called when self.trial_id is truthy (checked in receive_event)
            assert self.trial_id is not None
            span = create_span_from_event(
                trial_id=self.trial_id,
                actor_id=actor_id,
                operation_name=event_type,
                start_time=timestamp_override or event.timestamp,
                extra_tags=tags,
            )
            emit_span(span)

            # Progress logging
            DataHub._trace_emit_count += 1
            if DataHub._trace_emit_count % 50 == 0:
                logger.info(
                    "DataHub trace emit progress: %d events emitted (%d errors) "
                    "[latest: event_type=%s, trial=%s, actor=%s]",
                    DataHub._trace_emit_count,
                    DataHub._trace_error_count,
                    event_type,
                    self.trial_id,
                    actor_id,
                )

        except Exception as e:
            DataHub._trace_error_count += 1
            # Don't let trace emission failures affect event processing
            logger.warning(
                "Failed to emit event span (#%d): %s: %s (event_type=%s, actor=%s)",
                DataHub._trace_error_count,
                type(e).__name__,
                e,
                event.event_type,
                actor_id,
            )

    async def _deliver_event(self, envelope: _EventEnvelope) -> None:
        """Persist, emit trace, and dispatch an event.

        Called by the gate when an event is ready for delivery.
        This is the full pipeline — persist to JSONL, emit to trace backend,
        then dispatch to subscribed handlers. Events are persisted in gated
        (lifecycle) order, not raw arrival order.
        """
        event = envelope.event

        # Persist event (skip during backtest mode)
        if not self._backtest_mode:
            await self._persist_event(event)

        # Emit to trace backend if trial_id is set
        if self.trial_id and not self._backtest_mode:
            self._emit_event_span(
                event,
                envelope.source_actor_id,
                envelope.sport_type,
                envelope.game_id,
                envelope.game_date,
            )

        # Dispatch to subscribed handlers
        await self._dispatch_event(event)

    async def _gated_dispatch(self, envelope: _EventEnvelope) -> None:
        """Dispatch event with lifecycle ordering gate.

        Ensures per-game event ordering:
        1. GameInitializeEvent always dispatched first (PENDING → PREGAME)
        2. PreGameInsightEvent + OddsUpdateEvent dispatched in PREGAME
        3. GameStartEvent transitions to LIVE, flushes remaining buffer
        4. All other events dispatched only in LIVE phase

        Events without a game_id bypass the gate entirely.
        """
        event = envelope.event
        event_game_id = getattr(event, "game_id", "") or ""

        if not event_game_id:
            # No game_id — not a SportEvent, deliver immediately
            await self._deliver_event(envelope)
            return

        phase = self._game_phases.get(event_game_id, _GamePhase.PENDING)

        if isinstance(event, GameInitializeEvent):
            if phase == _GamePhase.PENDING:
                self._game_phases[event_game_id] = _GamePhase.PREGAME
                await self._deliver_event(envelope)
                # Run pregame callbacks with stores paused so web searches
                # complete before any new poll events arrive.
                if self._on_game_initialized_callbacks:
                    logger.info(
                        "Pausing stores — waiting on pre-game events for game_id=%s",
                        event_game_id,
                    )
                    self._pause_connected_stores()
                    try:
                        await asyncio.gather(
                            *(
                                cb(event_game_id)
                                for cb in self._on_game_initialized_callbacks
                            )
                        )
                    finally:
                        self._resume_connected_stores()
                        logger.info(
                            "Pre-game events complete — resuming stores for game_id=%s",
                            event_game_id,
                        )
                await self._flush_pending_dispatch(event_game_id)
            else:
                # Already initialized — deliver normally
                await self._deliver_event(envelope)

        elif phase == _GamePhase.LIVE:
            await self._deliver_event(envelope)

        elif phase == _GamePhase.PREGAME:
            if isinstance(event, (PreGameInsightEvent, OddsUpdateEvent)):
                await self._deliver_event(envelope)
            elif isinstance(event, GameStartEvent):
                self._game_phases[event_game_id] = _GamePhase.LIVE
                await self._deliver_event(envelope)
                await self._flush_pending_dispatch(event_game_id)
            elif isinstance(event, GameResultEvent):
                # Concluded/historical game: GameStartEvent never fires (status already FINAL)
                # Transition to LIVE, flush buffered plays/drives, then deliver result
                logger.info(
                    "GameResultEvent in PREGAME for game %s - transitioning to LIVE",
                    event_game_id,
                )
                self._game_phases[event_game_id] = _GamePhase.LIVE
                await self._flush_pending_dispatch(event_game_id)
                await self._deliver_event(envelope)
                logger.info(
                    "GameResultEvent delivered for game %s, handlers: %s",
                    event_game_id,
                    list(self._event_handlers.get("event.game_result", [])),
                )
            else:
                self._pending_dispatch[event_game_id].append(envelope)
                await self._check_buffer_overflow(event_game_id)

        else:
            # PENDING — buffer everything
            self._pending_dispatch[event_game_id].append(envelope)
            await self._check_buffer_overflow(event_game_id)

    async def _flush_pending_dispatch(self, game_id: str) -> None:
        """Deliver buffered events for a game, respecting current phase.

        Called after phase transitions. Events that still don't qualify
        for delivery under the new phase are re-buffered.
        """
        pending = self._pending_dispatch.pop(game_id, [])
        if not pending:
            return

        logger.info("Flushing %d buffered events for game_id=%s", len(pending), game_id)

        phase = self._game_phases.get(game_id, _GamePhase.PENDING)
        still_pending: list[_EventEnvelope] = []

        for env in pending:
            if phase == _GamePhase.LIVE:
                await self._deliver_event(env)
            elif phase == _GamePhase.PREGAME:
                if isinstance(env.event, (PreGameInsightEvent, OddsUpdateEvent)):
                    await self._deliver_event(env)
                elif isinstance(env.event, GameStartEvent):
                    # GameStartEvent in buffer triggers LIVE transition
                    self._game_phases[game_id] = _GamePhase.LIVE
                    phase = _GamePhase.LIVE
                    await self._deliver_event(env)
                else:
                    still_pending.append(env)
            else:
                still_pending.append(env)

        if still_pending:
            self._pending_dispatch[game_id] = still_pending

    async def _check_buffer_overflow(self, game_id: str) -> None:
        """Force-transition to LIVE if buffer exceeds safety limit."""
        if len(self._pending_dispatch[game_id]) > self._max_pending_per_game:
            logger.warning(
                "Event buffer overflow (%d) for game_id=%s — "
                "force-transitioning to LIVE",
                len(self._pending_dispatch[game_id]),
                game_id,
            )
            self._game_phases[game_id] = _GamePhase.LIVE
            await self._flush_pending_dispatch(game_id)

    def _cache_event(self, event: DataEvent) -> None:
        """Cache event for late-joining subscribers.

        Delegates to SubscriptionManager for consistent caching.
        """
        self._subscription_manager._cache_event(event)

    def get_recent_events(
        self,
        event_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[DataEvent]:
        """Get recent events from the cache.

        Delegates to SubscriptionManager for consistent event retrieval.

        Args:
            event_types: Filter by event types (None = all types)
            limit: Maximum number of events to return

        Returns:
            List of recent events (newest first)
        """
        return self._subscription_manager.get_recent_events(event_types, limit)

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

        Delegates to SubscriptionManager for both:
        - Legacy callback handlers (synchronous)
        - New Subscription queues (for external agents)

        Args:
            event: Event to dispatch
        """
        event_type = event.event_type

        # Log dispatch for debugging
        if event_type in self._event_handlers:
            logger.debug(
                "Dispatching %s to %d handler(s)",
                event_type,
                len(self._event_handlers[event_type]),
            )

        # Delegate to subscription manager for both sync callbacks and async queues
        self._subscription_manager.dispatch_sync(event)

    def connect_store(self, store: "DataStore") -> None:
        """Connect a DataStore to this hub.

        Args:
            store: DataStore instance
        """
        # Capture store info for use in emit_wrapper closure
        store_id = store.store_id
        store_sport_type = store.sport_type
        # Get game_id from store's poll_identifier (authoritative source)
        store_game_id = store._poll_identifier.get(
            "espn_game_id", store._poll_identifier.get("game_id", "")
        )
        # Get game_date from store's poll_identifier (for trace metadata)
        store_game_date = store._poll_identifier.get("game_date", "")

        # Set the store's async event emitter so events from a single poll
        # cycle are awaited sequentially, preserving within-store ordering
        # (e.g., GameStartEvent before NBAPlayEvent in the same poll response).
        async def async_emit(event: DataEvent) -> None:
            await self.receive_event(
                event,
                source_actor_id=store_id,
                sport_type=store_sport_type,
                game_id=store_game_id,
                game_date=store_game_date,
            )

        store.set_async_event_emitter(async_emit)

        # Store DataHub reference in store so it can subscribe to events if needed
        if hasattr(store, "_data_hub"):
            store._data_hub = self

        # Track connected store for lifecycle management
        if store not in self._connected_stores:
            self._connected_stores.append(store)

    def add_on_game_initialized(
        self, callback: Callable[[str], Awaitable[None]]
    ) -> None:
        """Register a callback invoked after ``GameInitializeEvent``.

        Multiple callbacks can be registered; they all run concurrently
        via ``asyncio.gather`` while connected stores are paused.

        The callback receives the ``game_id`` string.  While the callbacks
        run, all connected stores are paused so no new poll events race
        with pre-game work (e.g., web searches).
        """
        self._on_game_initialized_callbacks.append(callback)

    def _pause_connected_stores(self) -> None:
        """Pause polling on all connected stores."""
        for store in self._connected_stores:
            store.pause_polling()

    def _resume_connected_stores(self) -> None:
        """Resume polling on all connected stores."""
        for store in self._connected_stores:
            store.resume_polling()

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

        # Sort events by game_timestamp (actual game time) when available,
        # falling back to timestamp (poll time) for events without game clock data
        self._backtest_events.sort(key=lambda e: e.game_timestamp or e.timestamp)

        # Compute time offset for trace emission (rebase first event to "now")
        if self._emit_backtest_traces and self._backtest_events:
            first_ts = self._backtest_events[0].timestamp
            self._backtest_time_offset = datetime.now(timezone.utc) - first_ts
            logger.info(
                "Backtest trace rebasing enabled: offset=%s (first event at %s)",
                self._backtest_time_offset,
                first_ts.isoformat(),
            )

    def enable_backtest_traces(self, trial_id: str) -> None:
        """Enable trace emission during backtest with rebased timestamps.

        When enabled, data events replayed from the JSONL file are emitted to
        the trace backend (Jaeger or SLS) with timestamps rebased so the first
        event starts at "now".  This makes replay trials visible in the Arena
        UI alongside agent-generated events that carry wall-clock timestamps.

        Must be called *before* ``start_backtest()`` so the time offset can be
        computed once events are loaded.

        Args:
            trial_id: Trial identifier for trace emission.
        """
        self.trial_id = trial_id
        self._emit_backtest_traces = True

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
            from dojozero.data import deserialize_data_event

            return deserialize_data_event(event_dict)
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

        # Emit rebased trace span if enabled
        if self._emit_backtest_traces and self.trial_id and self._backtest_time_offset:
            rebased_ts = event.timestamp + self._backtest_time_offset
            self._emit_event_span(
                event,
                actor_id="backtest",
                sport_type=getattr(event, "sport", ""),
                game_id=getattr(event, "game_id", ""),
                timestamp_override=rebased_ts,
            )

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
        self._emit_backtest_traces = False
        self._backtest_time_offset = None

    # Backward compatibility alias (deprecated)
    def stop_replay(self) -> None:
        """Deprecated: Use stop_backtest instead."""
        self.stop_backtest()

    async def start(self) -> None:
        """Start all connected stores (begin polling).

        This should be called after all stores are connected and configured
        (e.g., after poll identifiers are set).
        """
        logger.info(
            "DataHub '%s' starting %d connected stores",
            self.hub_id,
            len(self._connected_stores),
        )
        for store in self._connected_stores:
            logger.info("DataHub starting store: %s", store.store_id)
            await store.start_polling()
        logger.info("DataHub '%s' all stores started", self.hub_id)

    async def stop(self) -> None:
        """Stop all connected stores (stop polling)."""
        for store in self._connected_stores:
            await store.stop_polling()

    @property
    def subscription_manager(self) -> SubscriptionManager:
        """Access the subscription manager for external agent integration.

        The SubscriptionManager handles:
        - Event subscriptions with filtering and backpressure
        - Recent event caching for late-joining subscribers
        - Both legacy callback-based and new queue-based subscriptions
        """
        return self._subscription_manager
