"""Transport layer for DojoZero client.

Handles HTTP and SSE communication with the Gateway.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from dojozero_client._exceptions import (
    AuthenticationError,
    BettingClosedError,
    ConnectionError,
    InsufficientBalanceError,
    NotRegisteredError,
    RateLimitedError,
    RegistrationError,
    StaleReferenceError,
    StreamDisconnectedError,
)

logger = logging.getLogger(__name__)


class SSEEvent:
    """Parsed Server-Sent Event."""

    def __init__(
        self,
        event: str = "message",
        data: str = "",
        id: str | None = None,
        retry: int | None = None,
    ):
        self.event = event
        self.data = data
        self.id = id
        self.retry = retry

    def json(self) -> dict[str, Any]:
        """Parse data as JSON."""
        return json.loads(self.data)

    def __repr__(self) -> str:
        return f"SSEEvent(event={self.event!r}, id={self.id!r}, data={self.data[:50]!r}...)"


class GatewayTransport:
    """HTTP/SSE transport for Gateway communication.

    Handles:
    - REST API calls with proper error handling
    - SSE streaming with reconnection support
    - Authentication headers
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
    ):
        """Initialize transport.

        Args:
            base_url: Gateway base URL (e.g., "http://localhost:8080")
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.agent_id: str | None = None
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._last_event_id: str | None = None

    def set_agent_id(self, agent_id: str) -> None:
        """Set agent ID after registration.

        Args:
            agent_id: Agent ID from registration response
        """
        self.agent_id = agent_id
        # Update client headers if already initialized
        if self._client:
            self._client.headers.update(self._auth_headers())

    async def __aenter__(self) -> "GatewayTransport":
        """Enter async context."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers=self._auth_headers(),
        )
        return self

    async def __aexit__(self, *_args: Any) -> None:
        """Exit async context."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _auth_headers(self) -> dict[str, str]:
        """Get authentication headers."""
        if self.agent_id:
            return {"X-Agent-ID": self.agent_id}
        return {}

    def _get_client(self) -> httpx.AsyncClient:
        """Get the HTTP client, raising if not initialized."""
        if self._client is None:
            raise ConnectionError("Transport not initialized. Use 'async with'.")
        return self._client

    async def request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request to the Gateway.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g., "/agents")
            json: JSON body for POST/PUT
            params: Query parameters

        Returns:
            Response JSON

        Raises:
            Various DojoClientError subclasses based on response
        """
        client = self._get_client()

        try:
            response = await client.request(
                method=method,
                url=path,
                json=json,
                params=params,
            )
        except httpx.ConnectError as e:
            raise ConnectionError(f"Failed to connect to gateway: {e}") from e
        except httpx.TimeoutException as e:
            raise ConnectionError(f"Request timed out: {e}") from e

        return self._handle_response(response)

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        """Handle HTTP response, raising appropriate exceptions."""
        if response.status_code == 200:
            return response.json()

        # Try to parse error response
        details: dict[str, Any] = {}
        try:
            error_data = response.json()
            error = error_data.get("error", {})
            code = error.get("code", "UNKNOWN")
            message = error.get("message", response.text)
            details = error.get("details", {})
        except Exception:
            code = "UNKNOWN"
            message = response.text

        # Map status codes to exceptions
        if response.status_code == 401:
            raise AuthenticationError(message)
        elif response.status_code == 403:
            raise NotRegisteredError(message)
        elif response.status_code == 409:
            raise RegistrationError(message)
        elif response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitedError(
                message,
                retry_after=int(retry_after) if retry_after else None,
            )
        elif response.status_code == 400:
            if code == "STALE_REFERENCE":
                raise StaleReferenceError(message, code, details)
            elif code == "INSUFFICIENT_BALANCE":
                raise InsufficientBalanceError(message, code, details)
            elif code == "BETTING_CLOSED":
                raise BettingClosedError(message, code, details)
            else:
                from dojozero_client._exceptions import BetRejectedError

                raise BetRejectedError(message, code, details)
        else:
            raise ConnectionError(
                f"Unexpected response {response.status_code}: {message}"
            )

    async def stream_events(self) -> AsyncIterator[SSEEvent]:
        """Stream events via SSE.

        Yields:
            SSEEvent objects

        Raises:
            StreamDisconnectedError: If stream disconnects unexpectedly
        """
        client = self._get_client()
        headers = self._auth_headers().copy()
        headers["Accept"] = "text/event-stream"

        # Include Last-Event-ID for reconnection
        if self._last_event_id:
            headers["Last-Event-ID"] = self._last_event_id

        try:
            async with client.stream(
                "GET",
                "/events/stream",
                headers=headers,
                timeout=None,  # SSE streams are long-lived
            ) as response:
                if response.status_code != 200:
                    self._handle_response(response)

                async for event in self._parse_sse_stream(response):
                    if event.id:
                        self._last_event_id = event.id
                    yield event

        except httpx.ConnectError as e:
            raise StreamDisconnectedError(f"Stream connection failed: {e}") from e
        except httpx.ReadError as e:
            raise StreamDisconnectedError(f"Stream read error: {e}") from e

    async def _parse_sse_stream(
        self,
        response: httpx.Response,
    ) -> AsyncIterator[SSEEvent]:
        """Parse SSE stream from response.

        Args:
            response: Streaming HTTP response

        Yields:
            Parsed SSE events
        """
        event_type = "message"
        data_lines: list[str] = []
        event_id: str | None = None
        retry: int | None = None

        async for line in response.aiter_lines():
            line = line.rstrip("\r\n")

            if not line:
                # Empty line = dispatch event
                if data_lines:
                    yield SSEEvent(
                        event=event_type,
                        data="\n".join(data_lines),
                        id=event_id,
                        retry=retry,
                    )
                # Reset for next event
                event_type = "message"
                data_lines = []
                event_id = None
                retry = None
                continue

            if line.startswith(":"):
                # Comment, ignore
                continue

            if ":" in line:
                field, _, value = line.partition(":")
                value = value.lstrip(" ")
            else:
                field = line
                value = ""

            if field == "event":
                event_type = value
            elif field == "data":
                data_lines.append(value)
            elif field == "id":
                event_id = value
            elif field == "retry":
                try:
                    retry = int(value)
                except ValueError:
                    pass


__all__ = [
    "GatewayTransport",
    "SSEEvent",
]
