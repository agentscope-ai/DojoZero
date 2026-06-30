"""Dashboard-level ModelScope AgentID verification (``/api/agents/whoami``).

Hub identity is deployment-wide, so the dashboard verifies agent JWTs directly —
no trial/gateway required. Uses the real ``agent_id_service_sdk.Verifier`` with a
locally-injected JWKS key (fully offline); skips when the optional SDK isn't
installed. Mirrors the gateway-side tests in ``test_gateway_agentid.py``.
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from dojozero.dashboard_server._server import create_dashboard_app

_AUD = "hub_test"
_ISS = "https://pre.modelscope.cn/openapi/v1"
_KID = "local-test-kid"
_SUB = "agent_id:modelscope:agent_real"


def _app(verifier):
    """Dashboard app (no lifespan needed) with ``agentid_verifier`` injected."""
    app = create_dashboard_app(
        orchestrator=MagicMock(),
        scheduler_store=MagicMock(),
        no_scheduler=True,
        enable_gateway=False,
    )
    app.state.agentid_verifier = verifier  # None → endpoint returns 503
    return TestClient(app)


def _real_verifier_with_local_key():
    """Real Verifier whose only JWKS key is one we control (offline, no network)."""
    pytest.importorskip("agent_id_service_sdk")
    from agent_id_service_sdk import (
        Verifier,
    )
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    v = Verifier(
        trusted_providers=["pre.modelscope.cn"],
        audience=_AUD,
        jwks_urls={"pre.modelscope.cn": "https://unused.invalid/jwks"},
        dpop_mode="disabled",
    )
    v._jwks_cache["pre.modelscope.cn"] = ({_KID: key.public_key()}, time.time())
    return v, key


def _sign(key, *, aud=_AUD, sub=_SUB, exp_delta=300):
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
        key,
        algorithm="EdDSA",
        headers={"kid": _KID},
    )


def test_whoami_no_verifier_returns_503():
    """AgentID unconfigured on the dashboard → 503 (not a crash)."""
    assert _app(None).get("/api/agents/whoami").status_code == 503


def test_whoami_valid_token():
    """A valid token authenticates at the dashboard with no trial running."""
    verifier, key = _real_verifier_with_local_key()
    resp = _app(verifier).get(
        "/api/agents/whoami", headers={"Authorization": f"Bearer {_sign(key)}"}
    )
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == _SUB


def test_whoami_no_auth_returns_401():
    verifier, _ = _real_verifier_with_local_key()
    assert _app(verifier).get("/api/agents/whoami").status_code == 401


def test_whoami_garbage_bearer_returns_401():
    verifier, _ = _real_verifier_with_local_key()
    resp = _app(verifier).get(
        "/api/agents/whoami", headers={"Authorization": "Bearer not.a.jwt"}
    )
    assert resp.status_code == 401


def test_whoami_wrong_audience_returns_401():
    verifier, key = _real_verifier_with_local_key()
    resp = _app(verifier).get(
        "/api/agents/whoami",
        headers={"Authorization": f"Bearer {_sign(key, aud='hub_WRONG')}"},
    )
    assert resp.status_code == 401
