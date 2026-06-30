"""Tests for ModelScope AgentID token attachment in GatewayTransport."""

from __future__ import annotations

import httpx
import pytest

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


@pytest.mark.asyncio
async def test_attaches_bearer_from_agentid_client():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["xid"] = request.headers.get("x-agent-id")
        return httpx.Response(200, json={"ok": True})

    fake = _FakeAgentIDClient(token="abc.def")
    transport = _transport_with(
        handler, agentid_client=fake, agentid_audience="hub_4abb08"
    )

    result = await transport.request("GET", "/balance")
    assert result == {"ok": True}
    # Bearer token from the AgentID client; no X-Agent-ID.
    assert captured["auth"] == "Bearer abc.def"
    assert captured["xid"] is None
    # Token was requested for the configured audience.
    assert fake.calls == ["hub_4abb08"]


@pytest.mark.asyncio
async def test_falls_back_to_x_agent_id_without_agentid_client():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["xid"] = request.headers.get("x-agent-id")
        return httpx.Response(200, json={"ok": True})

    transport = _transport_with(handler)
    transport.agent_id = "agent-bob"

    await transport.request("GET", "/balance")
    assert captured["xid"] == "agent-bob"
    assert captured["auth"] is None


@pytest.mark.asyncio
async def test_set_agent_id_does_not_leak_x_agent_id_in_agentid_mode():
    """set_agent_id must not write X-Agent-ID to client headers in AgentID mode."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["xid"] = request.headers.get("x-agent-id")
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    fake = _FakeAgentIDClient(token="abc.def")
    transport = _transport_with(handler, agentid_client=fake, agentid_audience="hub_x")
    transport.set_agent_id(
        "agent-leaked"
    )  # would leak as a client-level header pre-fix

    await transport.request("GET", "/balance")
    assert captured["xid"] is None
    assert captured["auth"] == "Bearer abc.def"


@pytest.mark.asyncio
async def test_offline_unregister_passes_agentid_client(monkeypatch):
    """DojoClient.unregister_agent (offline leave path) threads the AgentID client
    into the transport, so the DELETE carries a Bearer instead of X-Agent-ID.

    Without this, an AgentID gateway 401s an offline `leave --unregister`.
    """
    from dojozero_client import _client

    recorded: dict = {}

    class _FakeTransport:
        def __init__(
            self, base_url, timeout=30.0, agentid_client=None, agentid_audience=None
        ):
            recorded["agentid_client"] = agentid_client
            recorded["agentid_audience"] = agentid_audience

        def set_agent_id(self, agent_id):
            recorded["agent_id"] = agent_id

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, path, json=None):
            recorded.update(method=method, path=path, json=json)
            return {"message": "Unregistered successfully"}

    monkeypatch.setattr(_client, "GatewayTransport", _FakeTransport)

    fake_agentid = _FakeAgentIDClient()
    out = await _client.DojoClient.unregister_agent(
        "http://gw",
        "agent_x",
        "sk-1",
        agentid_client=fake_agentid,
        agentid_audience="hub_x",
    )
    assert out == {"message": "Unregistered successfully"}
    assert recorded["agentid_client"] is fake_agentid
    assert recorded["agentid_audience"] == "hub_x"
    assert recorded["method"] == "DELETE"
    assert recorded["path"] == "/agents/agent_x"
    assert recorded["json"] == {"sessionKey": "sk-1"}
