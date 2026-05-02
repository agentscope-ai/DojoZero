"""Tests for AgentID auth in ``GatewayTransport``.

The transport authenticates every request via ``Authorization: Bearer
<token>`` minted through ``agent-id-client-sdk``. Tests cover:
- Bearer header is attached when ``agentid_client`` is set.
- No header is sent when ``agentid_client`` is omitted (the gateway will
  return 401 — the desired failure mode for misconfigured runners).
- ``agentid_audience`` defaults to ``base_url`` and can be overridden.
- ``set_agent_id`` records the value but the transport never sends it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from dojozero_client._transport import GatewayTransport


def _stub_response(json_body: dict | None = None) -> httpx.Response:
    """Build a 200-OK httpx Response with a JSON body."""
    return httpx.Response(
        status_code=200,
        json=json_body or {"ok": True},
        request=httpx.Request("GET", "http://example.test/"),
    )


@pytest.mark.asyncio
async def test_request_sends_no_auth_header_when_agentid_client_missing():
    """No Bearer client → no auth header. Gateway will 401, which is the point."""
    transport = GatewayTransport(base_url="https://api.dojozero.live")
    transport.set_agent_id("recorded-but-not-sent")

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_stub_response())
        mock_client.aclose = AsyncMock()
        mock_client_class.return_value = mock_client

        async with transport:
            await transport.request("GET", "/balance")

        kwargs = mock_client.request.await_args.kwargs
        assert kwargs["headers"] == {}


@pytest.mark.asyncio
async def test_request_uses_bearer_token_when_agentid_client_set():
    agentid_client = AsyncMock()
    agentid_client.get_token = AsyncMock(return_value="signed-jwt")

    transport = GatewayTransport(
        base_url="https://api.dojozero.live",
        agentid_client=agentid_client,
    )

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_stub_response())
        mock_client.aclose = AsyncMock()
        mock_client_class.return_value = mock_client

        async with transport:
            await transport.request("GET", "/balance")

        # Audience defaults to base_url
        agentid_client.get_token.assert_awaited_once_with("https://api.dojozero.live")
        kwargs = mock_client.request.await_args.kwargs
        assert kwargs["headers"] == {"Authorization": "Bearer signed-jwt"}


@pytest.mark.asyncio
async def test_agentid_audience_override():
    agentid_client = AsyncMock()
    agentid_client.get_token = AsyncMock(return_value="t")

    transport = GatewayTransport(
        base_url="https://internal-gw.example.test",
        agentid_client=agentid_client,
        agentid_audience="https://api.dojozero.live",
    )

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_stub_response())
        mock_client.aclose = AsyncMock()
        mock_client_class.return_value = mock_client

        async with transport:
            await transport.request("GET", "/balance")

        agentid_client.get_token.assert_awaited_once_with("https://api.dojozero.live")


@pytest.mark.asyncio
async def test_x_agent_id_is_never_sent():
    """set_agent_id stores the value but the transport must not emit it."""
    agentid_client = AsyncMock()
    agentid_client.get_token = AsyncMock(return_value="t")

    transport = GatewayTransport(
        base_url="https://api.dojozero.live",
        agentid_client=agentid_client,
    )
    transport.set_agent_id("recorded-but-not-sent")

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_stub_response())
        mock_client.aclose = AsyncMock()
        mock_client_class.return_value = mock_client

        async with transport:
            await transport.request("GET", "/balance")

        kwargs = mock_client.request.await_args.kwargs
        assert kwargs["headers"] == {"Authorization": "Bearer t"}
        assert "X-Agent-ID" not in kwargs["headers"]


@pytest.mark.asyncio
async def test_token_fetched_per_request_so_refresh_is_transparent():
    """AIPClient handles caching internally; we just call get_token each time."""
    agentid_client = AsyncMock()
    # Simulate the AgentID Client returning a refreshed token on the second call.
    agentid_client.get_token = AsyncMock(side_effect=["token-v1", "token-v2"])

    transport = GatewayTransport(
        base_url="https://api.dojozero.live",
        agentid_client=agentid_client,
    )

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_stub_response())
        mock_client.aclose = AsyncMock()
        mock_client_class.return_value = mock_client

        async with transport:
            await transport.request("GET", "/balance")
            await transport.request("GET", "/balance")

        assert agentid_client.get_token.await_count == 2
        first_headers = mock_client.request.await_args_list[0].kwargs["headers"]
        second_headers = mock_client.request.await_args_list[1].kwargs["headers"]
        assert first_headers["Authorization"] == "Bearer token-v1"
        assert second_headers["Authorization"] == "Bearer token-v2"


@pytest.mark.asyncio
async def test_set_agent_id_records_but_does_not_emit():
    """set_agent_id stores the id for property reads but the transport never sends it."""
    agentid_client = AsyncMock()
    agentid_client.get_token = AsyncMock(return_value="t")

    transport = GatewayTransport(
        base_url="https://api.dojozero.live",
        agentid_client=agentid_client,
    )
    transport.set_agent_id("recorded-but-unused")

    # Property still returns it for callers that read transport.agent_id
    assert transport.agent_id == "recorded-but-unused"

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_stub_response())
        mock_client.aclose = AsyncMock()
        mock_client_class.return_value = mock_client

        async with transport:
            await transport.request("GET", "/balance")

        kwargs = mock_client.request.await_args.kwargs
        assert kwargs["headers"] == {"Authorization": "Bearer t"}
