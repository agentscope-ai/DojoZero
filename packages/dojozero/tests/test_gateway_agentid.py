"""Tests for ModelScope AgentID integration in the gateway.

The per-request / registration logic is exercised with a fake verifier (the
real ``agent_id_service_sdk.Verifier`` is tested in that package), so these
run without the optional SDK installed. The env-builder's "builds a verifier"
path is guarded with ``importorskip``.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from dojozero.gateway._agentid import agentid_verifier_from_env
from dojozero.gateway._server import GatewayState, get_agent_id


class _Verified:
    def __init__(self, agent_id: str, agent_name: str = "") -> None:
        self.agent_id = agent_id
        self.agent_name = agent_name


class _FakeVerifier:
    """Stand-in for ``agent_id_service_sdk.Verifier`` (no SDK dependency)."""

    def __init__(
        self, agent_id: str | None = None, error: Exception | None = None
    ) -> None:
        self._agent_id = agent_id
        self._error = error
        self.calls: list[str] = []

    async def verify(self, authorization_header: str) -> _Verified:
        self.calls.append(authorization_header)
        if self._error is not None:
            raise self._error
        return _Verified(self._agent_id or "aip:localhost:agent_x")


def _state(verifier=None) -> GatewayState:
    # get_agent_id only reads state.agentid_verifier; the rest can be dummies.
    return GatewayState(
        trial_id="t",
        data_hub=None,  # type: ignore[arg-type]
        broker=None,  # type: ignore[arg-type]
        adapter=None,  # type: ignore[arg-type]
        agentid_verifier=verifier,
    )


def test_get_agent_id_verifies_bearer():
    verifier = _FakeVerifier(agent_id="aip:localhost:agent_42")
    agent_id = asyncio.run(
        get_agent_id(
            x_agent_id=None, authorization="Bearer the.jwt", state=_state(verifier)
        )
    )
    assert agent_id == "aip:localhost:agent_42"
    assert verifier.calls == ["Bearer the.jwt"]


def test_get_agent_id_rejects_invalid_bearer():
    verifier = _FakeVerifier(error=ValueError("bad signature"))
    try:
        asyncio.run(
            get_agent_id(
                x_agent_id=None, authorization="Bearer bad", state=_state(verifier)
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("expected 401")


def test_bearer_takes_precedence_over_header():
    """A verified token is authoritative — a spoofed X-Agent-ID can't override."""
    verifier = _FakeVerifier(agent_id="aip:localhost:agent_token")
    agent_id = asyncio.run(
        get_agent_id(
            x_agent_id="spoofed-header-id",
            authorization="Bearer the.jwt",
            state=_state(verifier),
        )
    )
    assert agent_id == "aip:localhost:agent_token"


def test_falls_back_to_header_without_verifier():
    agent_id = asyncio.run(
        get_agent_id(x_agent_id="agent-bob", authorization=None, state=_state(None))
    )
    assert agent_id == "agent-bob"


def test_verifier_set_rejects_bare_x_agent_id():
    """With a verifier configured, a bare X-Agent-ID (no Bearer) is rejected.

    Honoring the unverified header would let a caller impersonate a registered
    agent — so AgentID-enabled gateways require a Bearer token.
    """
    verifier = _FakeVerifier(agent_id="should-not-be-used")
    try:
        asyncio.run(
            get_agent_id(
                x_agent_id="agent-carol", authorization=None, state=_state(verifier)
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("expected 401")
    assert verifier.calls == []


def test_requires_some_identity():
    try:
        asyncio.run(
            get_agent_id(x_agent_id=None, authorization=None, state=_state(None))
        )
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("expected 401")


def test_verifier_from_env_unconfigured(monkeypatch):
    monkeypatch.delenv("DOJOZERO_AGENTID_TRUSTED_PROVIDERS", raising=False)
    monkeypatch.delenv("DOJOZERO_AGENTID_AUDIENCE", raising=False)
    assert agentid_verifier_from_env() is None


def test_verifier_from_env_partial_returns_none(monkeypatch):
    monkeypatch.setenv("DOJOZERO_AGENTID_TRUSTED_PROVIDERS", "pre.modelscope.cn")
    monkeypatch.delenv("DOJOZERO_AGENTID_AUDIENCE", raising=False)
    assert agentid_verifier_from_env() is None


def test_verifier_from_env_builds(monkeypatch):
    pytest.importorskip("agent_id_service_sdk")
    monkeypatch.setenv("DOJOZERO_AGENTID_TRUSTED_PROVIDERS", "pre.modelscope.cn")
    monkeypatch.setenv("DOJOZERO_AGENTID_AUDIENCE", "hub_4abb08")
    monkeypatch.setenv(
        "DOJOZERO_AGENTID_JWKS_URLS",
        '{"pre.modelscope.cn": '
        '"https://pre.modelscope.cn/openapi/v1/agent_id/.well-known/agentid-jwks"}',
    )
    verifier = agentid_verifier_from_env()
    assert verifier is not None
    assert verifier._audience == "hub_4abb08"
    assert "pre.modelscope.cn" in verifier._trusted_providers
    assert "pre.modelscope.cn" in verifier._jwks_urls
