"""Tests for AIP auth in ``GatewayTransport``.

Covers the optional ``aip_client`` parameter:
- ``Authorization: AIP <token>`` is attached when ``aip_client`` is set.
- Legacy ``X-Agent-ID`` path still works when ``aip_client`` is None.
- ``aip_audience`` defaults to ``base_url`` and can be overridden.
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
async def test_request_uses_x_agent_id_when_no_aip_client():
    transport = GatewayTransport(base_url="https://api.dojozero.live")
    transport.set_agent_id("legacy-agent-1")

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_stub_response())
        mock_client.aclose = AsyncMock()
        mock_client_class.return_value = mock_client

        async with transport:
            await transport.request("GET", "/balance")

        kwargs = mock_client.request.await_args.kwargs
        assert kwargs["headers"] == {"X-Agent-ID": "legacy-agent-1"}


@pytest.mark.asyncio
async def test_request_uses_aip_token_when_aip_client_set():
    aip_client = AsyncMock()
    aip_client.get_token = AsyncMock(return_value="signed-jwt")

    transport = GatewayTransport(
        base_url="https://api.dojozero.live",
        aip_client=aip_client,
    )

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_stub_response())
        mock_client.aclose = AsyncMock()
        mock_client_class.return_value = mock_client

        async with transport:
            await transport.request("GET", "/balance")

        # Audience defaults to base_url
        aip_client.get_token.assert_awaited_once_with("https://api.dojozero.live")
        kwargs = mock_client.request.await_args.kwargs
        assert kwargs["headers"] == {"Authorization": "AIP signed-jwt"}


@pytest.mark.asyncio
async def test_aip_audience_override():
    aip_client = AsyncMock()
    aip_client.get_token = AsyncMock(return_value="t")

    transport = GatewayTransport(
        base_url="https://internal-gw.example.test",
        aip_client=aip_client,
        aip_audience="https://api.dojozero.live",
    )

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_stub_response())
        mock_client.aclose = AsyncMock()
        mock_client_class.return_value = mock_client

        async with transport:
            await transport.request("GET", "/balance")

        aip_client.get_token.assert_awaited_once_with("https://api.dojozero.live")


@pytest.mark.asyncio
async def test_aip_takes_precedence_over_x_agent_id():
    """Setting both should send AIP, not X-Agent-ID — JWT is the stronger claim."""
    aip_client = AsyncMock()
    aip_client.get_token = AsyncMock(return_value="t")

    transport = GatewayTransport(
        base_url="https://api.dojozero.live",
        aip_client=aip_client,
    )
    transport.set_agent_id("legacy-also-set")

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_stub_response())
        mock_client.aclose = AsyncMock()
        mock_client_class.return_value = mock_client

        async with transport:
            await transport.request("GET", "/balance")

        kwargs = mock_client.request.await_args.kwargs
        assert "Authorization" in kwargs["headers"]
        assert "X-Agent-ID" not in kwargs["headers"]


@pytest.mark.asyncio
async def test_token_fetched_per_request_so_refresh_is_transparent():
    """AIPClient handles caching internally; we just call get_token each time."""
    aip_client = AsyncMock()
    # Simulate the AIPClient returning a refreshed token on the second call.
    aip_client.get_token = AsyncMock(side_effect=["token-v1", "token-v2"])

    transport = GatewayTransport(
        base_url="https://api.dojozero.live",
        aip_client=aip_client,
    )

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_stub_response())
        mock_client.aclose = AsyncMock()
        mock_client_class.return_value = mock_client

        async with transport:
            await transport.request("GET", "/balance")
            await transport.request("GET", "/balance")

        assert aip_client.get_token.await_count == 2
        first_headers = mock_client.request.await_args_list[0].kwargs["headers"]
        second_headers = mock_client.request.await_args_list[1].kwargs["headers"]
        assert first_headers["Authorization"] == "AIP token-v1"
        assert second_headers["Authorization"] == "AIP token-v2"


@pytest.mark.asyncio
async def test_set_agent_id_is_noop_under_aip():
    """When AIP is configured, set_agent_id stores the id but it's not sent."""
    aip_client = AsyncMock()
    aip_client.get_token = AsyncMock(return_value="t")

    transport = GatewayTransport(
        base_url="https://api.dojozero.live",
        aip_client=aip_client,
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
        assert kwargs["headers"] == {"Authorization": "AIP t"}
