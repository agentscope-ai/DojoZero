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


# ---------------------------------------------------------------------------
# HTTP-layer enforcement with the REAL ``agent_id_service_sdk.Verifier``.
#
# Drives the gateway's ``get_agent_id`` dependency through FastAPI's TestClient
# using a genuine Verifier — but fully offline: a locally generated Ed25519 key
# is injected into the verifier's JWKS cache, so the verify path uses it without
# any network call, and tokens are signed with that key. No live ModelScope, no
# running trial, no AccessToken. These skip when the optional
# ``agent-id-service-sdk`` extra isn't installed (``pip install dojozero[agentid]``).
# ---------------------------------------------------------------------------

_AUD = "hub_test"
_ISS = "https://pre.modelscope.cn/openapi/v1"
_KID = "local-test-kid"
_SUB = "aip:identity.modelscope.cn:agent_real"


def _real_verifier_with_local_key():
    """Build a real Verifier whose only JWKS key is one we control (offline).

    Pre-seeding ``_jwks_cache`` (white-box, but stable) makes the verify path
    use our key without fetching ModelScope. Returns ``(verifier, signing_key)``.
    """
    pytest.importorskip("agent_id_service_sdk")
    import time

    from agent_id_service_sdk import (  # pyright: ignore[reportMissingImports]
        Verifier,
    )
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    signing_key = Ed25519PrivateKey.generate()
    verifier = Verifier(
        trusted_providers=["pre.modelscope.cn"],
        audience=_AUD,
        # Never fetched (cache is pre-seeded); points at an unresolvable host so
        # an accidental network attempt fails loudly instead of going live.
        jwks_urls={"pre.modelscope.cn": "https://unused.invalid/jwks"},
        dpop_mode="disabled",
    )
    verifier._jwks_cache["pre.modelscope.cn"] = (
        {_KID: signing_key.public_key()},
        time.time(),
    )
    return verifier, signing_key


def _sign(signing_key, *, aud=_AUD, sub=_SUB, exp_delta=300):
    import time
    import uuid

    import jwt as pyjwt

    now = int(time.time())
    return pyjwt.encode(
        {
            "iss": _ISS,
            "sub": sub,
            "aud": aud,
            "iat": now,
            "exp": now + exp_delta,
            "jti": "jti-" + uuid.uuid4().hex,
        },
        signing_key,
        algorithm="EdDSA",
        headers={"kid": _KID},
    )


def _whoami(verifier, headers=None):
    """Call a route guarded by the real ``get_agent_id`` over a stub gateway state."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from dojozero.gateway._server import get_agent_id, get_gateway_state

    app = FastAPI()

    @app.get("/whoami")
    async def whoami(agent_id: str = Depends(get_agent_id)):
        return {"agent_id": agent_id}

    state = _state(verifier)
    app.dependency_overrides[get_gateway_state] = lambda: state
    return TestClient(app).get("/whoami", headers=headers or {})


def test_http_no_auth_rejected():
    verifier, _ = _real_verifier_with_local_key()
    assert _whoami(verifier).status_code == 401


def test_http_bare_x_agent_id_rejected():
    """With a verifier configured, a bare X-Agent-ID can't impersonate (HTTP layer)."""
    verifier, _ = _real_verifier_with_local_key()
    assert _whoami(verifier, {"X-Agent-ID": "victim"}).status_code == 401


def test_http_garbage_bearer_rejected():
    verifier, _ = _real_verifier_with_local_key()
    assert _whoami(verifier, {"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_http_wrong_audience_rejected():
    verifier, key = _real_verifier_with_local_key()
    bearer = {"Authorization": f"Bearer {_sign(key, aud='hub_WRONG')}"}
    assert _whoami(verifier, bearer).status_code == 401


def test_http_valid_bearer_accepted():
    verifier, key = _real_verifier_with_local_key()
    resp = _whoami(verifier, {"Authorization": f"Bearer {_sign(key)}"})
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == _SUB


def test_http_token_sub_beats_spoofed_header():
    """A verified token is authoritative even when X-Agent-ID claims someone else."""
    verifier, key = _real_verifier_with_local_key()
    resp = _whoami(
        verifier,
        {"Authorization": f"Bearer {_sign(key)}", "X-Agent-ID": "victim"},
    )
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == _SUB


def test_http_legacy_x_agent_id_without_verifier():
    """No verifier configured: legacy X-Agent-ID is honored over HTTP."""
    resp = _whoami(None, {"X-Agent-ID": "legacy"})
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == "legacy"
