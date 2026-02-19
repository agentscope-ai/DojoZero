"""Server-Sent Events (SSE) transport for event streaming.

Provides SSE streaming for external agents to receive real-time events
from a trial's DataHub.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncIterator

from starlette.responses import StreamingResponse

from dojozero.gateway._models import EventEnvelope, HeartbeatMessage

if TYPE_CHECKING:
    from dojozero.data._subscriptions import Subscription

logger = logging.getLogger(__name__)


class SSEConnection:
    """Manages a single SSE connection for an external agent.

    Wraps a Subscription and converts events to SSE-formatted strings
    for HTTP streaming.
    """

    def __init__(
        self,
        subscription: "Subscription",
        trial_id: str,
        heartbeat_interval: float = 15.0,
    ):
        """Initialize SSE connection.

        Args:
            subscription: Subscription to stream events from
            trial_id: Trial ID for event envelope
            heartbeat_interval: Seconds between heartbeat messages
        """
        self.subscription = subscription
        self.trial_id = trial_id
        self.heartbeat_interval = heartbeat_interval
        self._closed = False

    async def event_stream(self) -> AsyncIterator[str]:
        """Generate SSE events from subscription queue.

        Yields SSE-formatted strings including:
        - Event messages with sequence IDs
        - Heartbeat messages to keep connection alive

        Yields:
            SSE-formatted strings ready for HTTP streaming
        """
        logger.info(
            "SSE stream started: subscription=%s, trial=%s",
            self.subscription.subscription_id,
            self.trial_id,
        )

        try:
            while not self._closed:
                # Wait for event with timeout for heartbeat
                event = await self.subscription.get(timeout=self.heartbeat_interval)

                if event is None:
                    # Timeout - send heartbeat
                    heartbeat = HeartbeatMessage(
                        timestamp=datetime.now(timezone.utc),
                    )
                    yield self._format_sse(
                        event="heartbeat",
                        data=heartbeat.model_dump(mode="json"),
                    )
                    continue

                # Get sequence number
                sequence = self.subscription.get_next_sequence()

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
        last_event_id: Last-Event-ID header for reconnection (future use)

    Returns:
        Starlette StreamingResponse with SSE content type
    """
    # TODO: Handle last_event_id for reconnection replay
    _ = last_event_id

    return StreamingResponse(
        connection.event_stream(),
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
