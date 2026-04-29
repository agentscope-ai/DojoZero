"""Tests for AIP (Agent Identity Protocol) integration in the Gateway.

Covers:
- ``aip_verifier_from_env()`` env parsing.
- ``get_agent_id()`` precedence: AIP token > X-Agent-ID > 401.
- AIP error mapping (expired / invalid / untrusted) → HTTP 401 with the right
  ``ErrorCode``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aip_identity_verify import AIPAgent
from aip_identity_verify.errors import (
    AIPProviderUntrusted,
    AIPSignatureInvalid,
    AIPTokenExpired,
    AIPTokenInvalid,
)
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from dojozero.gateway._aip import aip_verifier_from_env
from dojozero.gateway._server import get_agent_id


# ============================================================================
# aip_verifier_from_env()
# ============================================================================


class TestAipVerifierFromEnv:
    """Env-driven AIPVerifier construction."""

    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("DOJOZERO_AIP_TRUSTED_PROVIDERS", raising=False)
        monkeypatch.delenv("DOJOZERO_AIP_AUDIENCE", raising=False)
        assert aip_verifier_from_env() is None

    def test_returns_none_on_partial_config_audience_only(self, monkeypatch, caplog):
        monkeypatch.delenv("DOJOZERO_AIP_TRUSTED_PROVIDERS", raising=False)
        monkeypatch.setenv("DOJOZERO_AIP_AUDIENCE", "https://api.dojozero.live")
        with caplog.at_level("WARNING"):
            assert aip_verifier_from_env() is None
        assert any("partially configured" in r.message for r in caplog.records)

    def test_returns_none_on_partial_config_providers_only(self, monkeypatch, caplog):
        monkeypatch.setenv("DOJOZERO_AIP_TRUSTED_PROVIDERS", "pre.agent-id.live")
        monkeypatch.delenv("DOJOZERO_AIP_AUDIENCE", raising=False)
        with caplog.at_level("WARNING"):
            assert aip_verifier_from_env() is None
        assert any("partially configured" in r.message for r in caplog.records)

    def test_builds_verifier_with_minimum_config(self, monkeypatch):
        monkeypatch.setenv("DOJOZERO_AIP_TRUSTED_PROVIDERS", "pre.agent-id.live")
        monkeypatch.setenv("DOJOZERO_AIP_AUDIENCE", "https://api.dojozero.live")
        verifier = aip_verifier_from_env()
        assert verifier is not None
        assert verifier._trusted_providers == ["pre.agent-id.live"]
        assert verifier._audience == "https://api.dojozero.live"
        assert verifier._cache_ttl == 3600
        assert verifier._clock_skew_seconds == 30

    def test_parses_multiple_trusted_providers(self, monkeypatch):
        monkeypatch.setenv(
            "DOJOZERO_AIP_TRUSTED_PROVIDERS",
            "pre.agent-id.live, agent-id.live ,localhost:8000",
        )
        monkeypatch.setenv("DOJOZERO_AIP_AUDIENCE", "https://api.dojozero.live")
        verifier = aip_verifier_from_env()
        assert verifier is not None
        assert verifier._trusted_providers == [
            "pre.agent-id.live",
            "agent-id.live",
            "localhost:8000",
        ]

    def test_reads_optional_overrides(self, monkeypatch):
        monkeypatch.setenv("DOJOZERO_AIP_TRUSTED_PROVIDERS", "pre.agent-id.live")
        monkeypatch.setenv("DOJOZERO_AIP_AUDIENCE", "https://api.dojozero.live")
        monkeypatch.setenv("DOJOZERO_AIP_CACHE_TTL_SECONDS", "60")
        monkeypatch.setenv("DOJOZERO_AIP_CLOCK_SKEW_SECONDS", "10")
        monkeypatch.setenv(
            "DOJOZERO_AIP_PROVIDER_URLS",
            '{"localhost:8000": "http://localhost:8000"}',
        )
        verifier = aip_verifier_from_env()
        assert verifier is not None
        assert verifier._cache_ttl == 60
        assert verifier._clock_skew_seconds == 10
        assert verifier._provider_urls == {"localhost:8000": "http://localhost:8000"}


# ============================================================================
# get_agent_id() via FastAPI dependency
# ============================================================================


def _make_app(aip_verifier=None) -> FastAPI:
    """Minimal FastAPI app exposing only ``get_agent_id`` for dependency tests."""
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(agent_id: str = Depends(get_agent_id)):
        return {"agent_id": agent_id}

    # Stub the same shape as a real GatewayState so get_agent_id's
    # `getattr(state, "aip_verifier", None)` lookup works.
    app.state.gateway_state = SimpleNamespace(aip_verifier=aip_verifier)
    return app


def _make_agent(agent_id: str = "aip:dojozero:degen-claude-canary") -> AIPAgent:
    """Build a minimal verified AIPAgent for stubbing the verifier."""
    return AIPAgent(
        agent_id=agent_id,
        agent_name="Degen Claude (Canary)",
        principal={"id": "principal_test", "type": "org", "name": "DojoZero"},
        capabilities=[],
        scopes={},
        delegation=None,
        model_info=None,
        issuer="https://pre.agent-id.live",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        raw_claims={},
    )


class TestGetAgentId:
    """``get_agent_id`` precedence and AIP error mapping."""

    def test_x_agent_id_header_returns_id(self):
        client = TestClient(_make_app(aip_verifier=None))
        resp = client.get("/whoami", headers={"X-Agent-ID": "legacy-agent-1"})
        assert resp.status_code == 200
        assert resp.json() == {"agent_id": "legacy-agent-1"}

    def test_no_credentials_401(self):
        client = TestClient(_make_app(aip_verifier=None))
        resp = client.get("/whoami")
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    def test_aip_token_with_verifier_returns_agent_id(self):
        verifier = AsyncMock()
        verifier.verify_token = AsyncMock(
            return_value=_make_agent("aip:dojozero:degen-claude-canary")
        )
        client = TestClient(_make_app(aip_verifier=verifier))

        resp = client.get("/whoami", headers={"Authorization": "AIP fake-token-bytes"})
        assert resp.status_code == 200
        assert resp.json() == {"agent_id": "aip:dojozero:degen-claude-canary"}
        verifier.verify_token.assert_awaited_once_with("fake-token-bytes")

    def test_aip_token_takes_precedence_over_x_agent_id(self):
        verifier = AsyncMock()
        verifier.verify_token = AsyncMock(
            return_value=_make_agent("aip:dojozero:from-token")
        )
        client = TestClient(_make_app(aip_verifier=verifier))

        resp = client.get(
            "/whoami",
            headers={
                "Authorization": "AIP fake-token",
                "X-Agent-ID": "should-be-ignored",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"agent_id": "aip:dojozero:from-token"}

    def test_aip_token_without_verifier_returns_401(self):
        client = TestClient(_make_app(aip_verifier=None))
        resp = client.get("/whoami", headers={"Authorization": "AIP fake-token"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"
        assert (
            "AIP authentication is not configured"
            in resp.json()["detail"]["error"]["message"]
        )

    def test_aip_expired_token_maps_to_token_expired(self):
        verifier = AsyncMock()
        verifier.verify_token = AsyncMock(
            side_effect=AIPTokenExpired("Signature has expired")
        )
        client = TestClient(_make_app(aip_verifier=verifier))

        resp = client.get("/whoami", headers={"Authorization": "AIP expired-token"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "TOKEN_EXPIRED"

    def test_aip_invalid_token_maps_to_invalid_token(self):
        verifier = AsyncMock()
        verifier.verify_token = AsyncMock(side_effect=AIPTokenInvalid("Malformed JWT"))
        client = TestClient(_make_app(aip_verifier=verifier))

        resp = client.get("/whoami", headers={"Authorization": "AIP malformed"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"

    def test_aip_untrusted_provider_maps_to_invalid_token(self):
        verifier = AsyncMock()
        verifier.verify_token = AsyncMock(
            side_effect=AIPProviderUntrusted(
                "Provider 'evil.example.com' is not trusted"
            )
        )
        client = TestClient(_make_app(aip_verifier=verifier))

        resp = client.get("/whoami", headers={"Authorization": "AIP token-from-evil"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"

    def test_aip_invalid_signature_maps_to_invalid_token(self):
        verifier = AsyncMock()
        verifier.verify_token = AsyncMock(
            side_effect=AIPSignatureInvalid("Signature verification failed")
        )
        client = TestClient(_make_app(aip_verifier=verifier))

        resp = client.get("/whoami", headers={"Authorization": "AIP tampered-token"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"

    @pytest.mark.parametrize("scheme", ["Bearer", "Basic"])
    def test_non_aip_authorization_falls_through_to_header_path(self, scheme):
        """A Bearer/Basic header should NOT trigger AIP path; X-Agent-ID still works."""
        verifier = AsyncMock()
        client = TestClient(_make_app(aip_verifier=verifier))

        resp = client.get(
            "/whoami",
            headers={
                "Authorization": f"{scheme} something",
                "X-Agent-ID": "legacy-fallthrough",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"agent_id": "legacy-fallthrough"}
        verifier.verify_token.assert_not_awaited()
