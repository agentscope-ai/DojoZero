"""Transport layer for DojoZero client.

Handles HTTP and SSE communication with the Gateway.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

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

if TYPE_CHECKING:
    from agent_id_client_sdk import AIPClient

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
        agentid_client: "AIPClient | None" = None,
        agentid_audience: str | None = None,
    ):
        """Initialize transport.

        Args:
            base_url: Gateway base URL (e.g., "http://localhost:8080")
            timeout: Request timeout in seconds
            agentid_client: ``AIPClient`` (alias for ``Client``) from
                ``agent-id-client-sdk``. The transport authenticates every
                request via ``Authorization: Bearer <token>`` minted through
                ``client.get_token(audience)`` — the SDK owns token caching
                and refresh. If omitted, no auth header is sent and the
                gateway will return 401.
            agentid_audience: Audience claim used when minting AgentID tokens.
                Defaults to ``base_url``, which matches the gateway's expected
                ``aud``. Override only if the gateway runs behind a proxy where
                the audience differs from the URL.
        """
        self.base_url = base_url.rstrip("/")
        self.agent_id: str | None = None
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._last_event_id: str | None = None
        self._agentid_client = agentid_client
        self._agentid_audience = agentid_audience or self.base_url

    def set_agent_id(self, agent_id: str) -> None:
        """Record the agent ID returned by registration.

        Stored on ``self.agent_id`` so callers can read it as a property.
        The transport does **not** send this in any header — the gateway
        derives identity from the verified JWT ``sub`` claim instead.

        Args:
            agent_id: Agent ID from registration response
        """
        self.agent_id = agent_id

    def set_last_event_id(self, event_id: int | str) -> None:
        """Set last event ID for reconnection replay.

        Call this before streaming to resume from a specific sequence.

        Args:
            event_id: Last event sequence seen (will be converted to string)
        """
        self._last_event_id = str(event_id)

    async def __aenter__(self) -> "GatewayTransport":
        """Enter async context."""
        # Auth headers are computed per-request so AgentID token refresh works
        # transparently without re-baking the client.
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *_args: Any) -> None:
        """Exit async context."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _auth_headers(self) -> dict[str, str]:
        """Build the auth header for the current request.

        Returns a fresh ``Authorization: Bearer <token>`` from the configured
        AgentID client (cached by the SDK; refreshed 60s before expiry). If
        no client is configured, returns no header — the gateway will reject
        the request with 401, which is the desired failure mode for
        misconfigured runners.
        """
        if self._agentid_client is None:
            return {}
        token = await self._agentid_client.get_token(self._agentid_audience)
        return {"Authorization": f"Bearer {token}"}

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
        headers = await self._auth_headers()

        try:
            response = await client.request(
                method=method,
                url=path,
                json=json,
                params=params,
                headers=headers,
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
        headers = await self._auth_headers()
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
