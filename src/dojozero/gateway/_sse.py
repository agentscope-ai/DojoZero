"""Server-Sent Events (SSE) transport for event streaming.

Provides SSE streaming for external agents to receive real-time events
from a trial's DataHub.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from starlette.responses import StreamingResponse

from dojozero.gateway._models import EventEnvelope, HeartbeatMessage, TrialEndedMessage

if TYPE_CHECKING:
    from dojozero.data._subscriptions import Subscription

logger = logging.getLogger(__name__)


class SSEConnection:
    """Manages a single SSE connection for an external agent.

    Wraps a Subscription and converts events to SSE-formatted strings
    for HTTP streaming. Supports reconnection replay via Last-Event-ID.
    """

    def __init__(
        self,
        subscription: "Subscription",
        trial_id: str,
        get_global_sequence: Callable[[], int],
        get_recent_events: Callable[[int], list[Any]],
        heartbeat_interval: float = 15.0,
        trial_ended_event: asyncio.Event | None = None,
        get_trial_ended_message: Callable[[], TrialEndedMessage | None] | None = None,
    ):
        """Initialize SSE connection.

        Args:
            subscription: Subscription to stream events from
            trial_id: Trial ID for event envelope
            get_global_sequence: Callable returning current global sequence number
            get_recent_events: Callable returning recent events (takes limit param)
            heartbeat_interval: Seconds between heartbeat messages
            trial_ended_event: Optional event to signal trial has ended
            get_trial_ended_message: Optional callable to get the trial ended message
        """
        self.subscription = subscription
        self.trial_id = trial_id
        self.get_global_sequence = get_global_sequence
        self.get_recent_events = get_recent_events
        self.heartbeat_interval = heartbeat_interval
        self._trial_ended_event = trial_ended_event
        self._get_trial_ended_message = get_trial_ended_message
        self._closed = False

    async def event_stream(
        self,
        last_event_id: int | None = None,
    ) -> AsyncIterator[str]:
        """Generate SSE events from subscription queue.

        If last_event_id is provided, replays missed events first before
        streaming live events. This enables reliable reconnection.

        Args:
            last_event_id: Last sequence seen by client (for reconnection replay)

        Yields:
            SSE-formatted strings including:
            - Replay events (if reconnecting)
            - Live event messages with sequence IDs
            - Heartbeat messages to keep connection alive
            - trial_ended message when trial completes
        """
        logger.info(
            "SSE stream started: subscription=%s, trial=%s, last_event_id=%s",
            self.subscription.subscription_id,
            self.trial_id,
            last_event_id,
        )

        try:
            # Replay missed events on reconnection
            if last_event_id is not None:
                for sse_msg in self._replay_events(last_event_id):
                    yield sse_msg

            while not self._closed:
                # Check if trial has ended
                if (
                    self._trial_ended_event is not None
                    and self._trial_ended_event.is_set()
                ):
                    # Send trial_ended message and close
                    if self._get_trial_ended_message is not None:
                        ended_msg = self._get_trial_ended_message()
                        if ended_msg is not None:
                            yield self._format_sse(
                                event="trial_ended",
                                data=ended_msg.model_dump(mode="json", by_alias=True),
                            )
                    logger.info(
                        "SSE stream sending trial_ended: subscription=%s",
                        self.subscription.subscription_id,
                    )
                    break

                # Wait for event with timeout for heartbeat
                event = await self.subscription.get(timeout=self.heartbeat_interval)

                if event is None:
                    # Timeout - check trial ended again before sending heartbeat
                    if (
                        self._trial_ended_event is not None
                        and self._trial_ended_event.is_set()
                    ):
                        continue  # Will be caught at top of loop
                    # Send heartbeat
                    heartbeat = HeartbeatMessage(
                        timestamp=datetime.now(timezone.utc),
                    )
                    yield self._format_sse(
                        event="heartbeat",
                        data=heartbeat.model_dump(mode="json"),
                    )
                    continue

                # Get global sequence number (for reference_sequence in bets)
                sequence = self.get_global_sequence()

                # Create envelope
                envelope = EventEnvelope(
                    trial_id=self.trial_id,
                    sequence=sequence,
                    timestamp=event.timestamp,
                    payload=event.to_dict(),
                )

                yield self._format_sse(
                    event="event",
                    data=envelope.model_dump(mode="json", by_alias=True),
                    id=str(sequence),
                )

        except asyncio.CancelledError:
            logger.debug(
                "SSE connection cancelled: subscription=%s",
                self.subscription.subscription_id,
            )
            raise
        except Exception as e:
            logger.error(
                "SSE stream error: subscription=%s, error=%s",
                self.subscription.subscription_id,
                e,
                exc_info=True,
            )
            raise
        finally:
            logger.info(
                "SSE stream ended: subscription=%s",
                self.subscription.subscription_id,
            )

    def _replay_events(self, last_event_id: int) -> list[str]:
        """Replay events missed since last_event_id.

        Args:
            last_event_id: Last sequence seen by client

        Returns:
            List of SSE-formatted strings for missed events
        """
        current_sequence = self.get_global_sequence()

        # No events to replay if client is caught up
        if last_event_id >= current_sequence:
            logger.debug(
                "No replay needed: last_event_id=%d >= current=%d",
                last_event_id,
                current_sequence,
            )
            return []

        # Get recent events from cache
        # Request enough to cover the gap, capped at reasonable limit
        gap = current_sequence - last_event_id
        limit = min(gap, 100)
        recent_events = self.get_recent_events(limit)

        # Build replay messages (events are newest-first, we want oldest-first)
        replay_messages = []
        for i, event in enumerate(reversed(recent_events)):
            # Calculate sequence (oldest event has lowest sequence)
            event_sequence = current_sequence - (len(recent_events) - 1 - i)
            if event_sequence <= last_event_id:
                continue  # Skip events client already has

            envelope = EventEnvelope(
                trial_id=self.trial_id,
                sequence=event_sequence,
                timestamp=event.timestamp,
                payload=event.to_dict(),
            )

            replay_messages.append(
                self._format_sse(
                    event="event",
                    data=envelope.model_dump(mode="json", by_alias=True),
                    id=str(event_sequence),
                )
            )

        logger.info(
            "Replaying %d events: last_event_id=%d, current=%d",
            len(replay_messages),
            last_event_id,
            current_sequence,
        )

        return replay_messages

    def close(self) -> None:
        """Mark connection as closed."""
        self._closed = True
        logger.debug(
            "SSE connection closed: subscription=%s",
            self.subscription.subscription_id,
        )

    @staticmethod
    def _format_sse(
        event: str,
        data: dict,
        id: str | None = None,
        retry: int | None = None,
    ) -> str:
        """Format data as SSE message.

        Args:
            event: Event type name
            data: Data payload to JSON-serialize
            id: Optional event ID for reconnection
            retry: Optional retry interval in milliseconds

        Returns:
            SSE-formatted string with trailing newlines
        """
        lines = []

        if id is not None:
            lines.append(f"id: {id}")

        lines.append(f"event: {event}")
        lines.append(f"data: {json.dumps(data, default=str)}")

        if retry is not None:
            lines.append(f"retry: {retry}")

        return "\n".join(lines) + "\n\n"


def create_sse_response(
    connection: SSEConnection,
    last_event_id: str | None = None,
) -> StreamingResponse:
    """Create SSE StreamingResponse.

    Args:
        connection: SSE connection to stream from
        last_event_id: Last-Event-ID header for reconnection replay

    Returns:
        Starlette StreamingResponse with SSE content type
    """
    # Parse last_event_id as sequence number for replay
    replay_from: int | None = None
    if last_event_id is not None:
        try:
            replay_from = int(last_event_id)
        except ValueError:
            logger.warning("Invalid Last-Event-ID: %s", last_event_id)

    return StreamingResponse(
        connection.event_stream(last_event_id=replay_from),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


__all__ = [
    "SSEConnection",
    "create_sse_response",
]
