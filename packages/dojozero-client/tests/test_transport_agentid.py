"""Tests for ModelScope AgentID token attachment in GatewayTransport."""

from __future__ import annotations

import asyncio

import httpx

from dojozero_client._transport import GatewayTransport


class _FakeAgentIDClient:
    """Stand-in for ``agent_id_client_sdk.Client`` (no SDK dependency)."""

    def __init__(self, token: str = "the.jwt") -> None:
        self._token = token
        self.calls: list[str | None] = []

    async def get_token(self, audience: str | None = None) -> str:
        self.calls.append(audience)
        return self._token


def _transport_with(handler, **kwargs) -> GatewayTransport:
    transport = GatewayTransport("http://gw", **kwargs)
    transport._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gw"
    )
    return transport


def test_attaches_bearer_from_agentid_client():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["xid"] = request.headers.get("x-agent-id")
        return httpx.Response(200, json={"ok": True})

    fake = _FakeAgentIDClient(token="abc.def")
    transport = _transport_with(
        handler, agentid_client=fake, agentid_audience="hub_4abb08"
    )

    result = asyncio.run(transport.request("GET", "/balance"))
    assert result == {"ok": True}
    # Bearer token from the AgentID client; no X-Agent-ID.
    assert captured["auth"] == "Bearer abc.def"
    assert captured["xid"] is None
    # Token was requested for the configured audience.
    assert fake.calls == ["hub_4abb08"]


def test_falls_back_to_x_agent_id_without_agentid_client():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["xid"] = request.headers.get("x-agent-id")
        return httpx.Response(200, json={"ok": True})

    transport = _transport_with(handler)
    transport.agent_id = "agent-bob"

    asyncio.run(transport.request("GET", "/balance"))
    assert captured["xid"] == "agent-bob"
    assert captured["auth"] is None
