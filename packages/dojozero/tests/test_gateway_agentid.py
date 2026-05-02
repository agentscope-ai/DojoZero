"""Tests for AgentID integration in the Gateway.

Covers:
- ``agentid_verifier_from_env()`` env parsing.
- ``get_agent_id()`` precedence: Bearer token > X-Agent-ID > 401.
- AgentID error mapping (expired / invalid / untrusted) → HTTP 401 with the
  right ``ErrorCode``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent_id_service_sdk import VerifiedAgent
from agent_id_service_sdk.errors import (
    ProviderUntrustedError,
    SignatureInvalidError,
    TokenExpiredError,
    TokenInvalidError,
)
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from dojozero.gateway._agentid import agentid_verifier_from_env
from dojozero.gateway._server import get_agent_id


# ============================================================================
# agentid_verifier_from_env()
# ============================================================================


class TestAipVerifierFromEnv:
    """Env-driven Verifier construction."""

    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("DOJOZERO_AGENTID_TRUSTED_PROVIDERS", raising=False)
        monkeypatch.delenv("DOJOZERO_AGENTID_AUDIENCE", raising=False)
        assert agentid_verifier_from_env() is None

    def test_returns_none_on_partial_config_audience_only(self, monkeypatch, caplog):
        monkeypatch.delenv("DOJOZERO_AGENTID_TRUSTED_PROVIDERS", raising=False)
        monkeypatch.setenv("DOJOZERO_AGENTID_AUDIENCE", "https://api.dojozero.live")
        with caplog.at_level("WARNING"):
            assert agentid_verifier_from_env() is None
        assert any("partially configured" in r.message for r in caplog.records)

    def test_returns_none_on_partial_config_providers_only(self, monkeypatch, caplog):
        monkeypatch.setenv("DOJOZERO_AGENTID_TRUSTED_PROVIDERS", "pre.agent-id.live")
        monkeypatch.delenv("DOJOZERO_AGENTID_AUDIENCE", raising=False)
        with caplog.at_level("WARNING"):
            assert agentid_verifier_from_env() is None
        assert any("partially configured" in r.message for r in caplog.records)

    def test_builds_verifier_with_minimum_config(self, monkeypatch):
        monkeypatch.setenv("DOJOZERO_AGENTID_TRUSTED_PROVIDERS", "pre.agent-id.live")
        monkeypatch.setenv("DOJOZERO_AGENTID_AUDIENCE", "https://api.dojozero.live")
        verifier = agentid_verifier_from_env()
        assert verifier is not None
        assert verifier._trusted_providers == ["pre.agent-id.live"]
        assert verifier._audience == "https://api.dojozero.live"
        assert verifier._cache_ttl == 3600
        assert verifier._clock_skew_seconds == 30
        # Activity reporting off when no API key set.
        assert verifier._activity_api_key is None
        assert verifier._report_auto_verify is False

    def test_parses_multiple_trusted_providers(self, monkeypatch):
        monkeypatch.setenv(
            "DOJOZERO_AGENTID_TRUSTED_PROVIDERS",
            "pre.agent-id.live, agent-id.live ,localhost:8000",
        )
        monkeypatch.setenv("DOJOZERO_AGENTID_AUDIENCE", "https://api.dojozero.live")
        verifier = agentid_verifier_from_env()
        assert verifier is not None
        assert verifier._trusted_providers == [
            "pre.agent-id.live",
            "agent-id.live",
            "localhost:8000",
        ]

    def test_reads_optional_overrides(self, monkeypatch):
        monkeypatch.setenv("DOJOZERO_AGENTID_TRUSTED_PROVIDERS", "pre.agent-id.live")
        monkeypatch.setenv("DOJOZERO_AGENTID_AUDIENCE", "https://api.dojozero.live")
        monkeypatch.setenv("DOJOZERO_AGENTID_CACHE_TTL_SECONDS", "60")
        monkeypatch.setenv("DOJOZERO_AGENTID_CLOCK_SKEW_SECONDS", "10")
        monkeypatch.setenv(
            "DOJOZERO_AGENTID_PROVIDER_URLS",
            '{"localhost:8000": "http://localhost:8000"}',
        )
        verifier = agentid_verifier_from_env()
        assert verifier is not None
        assert verifier._cache_ttl == 60
        assert verifier._clock_skew_seconds == 10
        assert verifier._provider_urls == {"localhost:8000": "http://localhost:8000"}

    def test_activity_reporting_enabled_when_api_key_set(self, monkeypatch):
        monkeypatch.setenv("DOJOZERO_AGENTID_TRUSTED_PROVIDERS", "pre.agent-id.live")
        monkeypatch.setenv("DOJOZERO_AGENTID_AUDIENCE", "https://api.dojozero.live")
        monkeypatch.setenv("DOJOZERO_AGENTID_ACTIVITY_API_KEY", "act_test_xxx")
        monkeypatch.setenv("DOJOZERO_AGENTID_AGENT_TOKEN", "gateway.jwt.token")
        monkeypatch.setenv("DOJOZERO_AGENTID_SERVICE_NAME", "dojozero-gateway-test")
        verifier = agentid_verifier_from_env()
        assert verifier is not None
        assert verifier._activity_api_key == "act_test_xxx"
        assert verifier._agent_token_for_emit == "gateway.jwt.token"
        assert verifier._service_name == "dojozero-gateway-test"
        assert verifier._report_auto_verify is True


# ============================================================================
# get_agent_id() via FastAPI dependency
# ============================================================================


def _make_app(agentid_verifier=None) -> FastAPI:
    """Minimal FastAPI app exposing only ``get_agent_id`` for dependency tests."""
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(agent_id: str = Depends(get_agent_id)):
        return {"agent_id": agent_id}

    # Stub the same shape as a real GatewayState so get_agent_id's
    # `getattr(state, "agentid_verifier", None)` lookup works.
    app.state.gateway_state = SimpleNamespace(agentid_verifier=agentid_verifier)
    return app


def _make_agent(
    agent_id: str = "agentid:dojozero:degen-claude-canary",
) -> VerifiedAgent:
    """Build a minimal VerifiedAgent for stubbing the verifier."""
    return VerifiedAgent(
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
    """``get_agent_id`` happy/sad paths.

    The gateway authenticates external agents via ``Authorization: Bearer
    <token>`` only — there is no fallback header path. Tests cover:
    success, missing/malformed Authorization, verifier not configured (503),
    and the four AgentID error → HTTP 401 mappings.
    """

    def test_bearer_token_returns_verified_agent_id(self):
        verifier = AsyncMock()
        verifier.verify_token = AsyncMock(
            return_value=_make_agent("agentid:dojozero:degen-claude-canary")
        )
        client = TestClient(_make_app(agentid_verifier=verifier))

        resp = client.get(
            "/whoami", headers={"Authorization": "Bearer fake-token-bytes"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"agent_id": "agentid:dojozero:degen-claude-canary"}
        verifier.verify_token.assert_awaited_once_with(
            "fake-token-bytes",
            request_context={"route": "/whoami"},
        )

    def test_no_authorization_header_returns_401(self):
        verifier = AsyncMock()
        client = TestClient(_make_app(agentid_verifier=verifier))
        resp = client.get("/whoami")
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"
        verifier.verify_token.assert_not_awaited()

    def test_x_agent_id_header_alone_returns_401(self):
        """Legacy header is no longer accepted — Bearer is the only auth path."""
        verifier = AsyncMock()
        client = TestClient(_make_app(agentid_verifier=verifier))
        resp = client.get("/whoami", headers={"X-Agent-ID": "legacy-agent-1"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"
        verifier.verify_token.assert_not_awaited()

    @pytest.mark.parametrize("scheme", ["AIP", "Basic", "Token"])
    def test_non_bearer_authorization_returns_401(self, scheme):
        """Only the Bearer scheme is accepted (AIP, Basic, etc. all rejected)."""
        verifier = AsyncMock()
        client = TestClient(_make_app(agentid_verifier=verifier))
        resp = client.get("/whoami", headers={"Authorization": f"{scheme} something"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"
        verifier.verify_token.assert_not_awaited()

    def test_bearer_without_verifier_returns_503(self):
        """No verifier configured means the operator misconfigured the gateway."""
        client = TestClient(_make_app(agentid_verifier=None))
        resp = client.get("/whoami", headers={"Authorization": "Bearer fake-token"})
        assert resp.status_code == 503
        assert (
            "AgentID provider is not configured"
            in resp.json()["detail"]["error"]["message"]
        )

    def test_expired_token_maps_to_token_expired(self):
        verifier = AsyncMock()
        verifier.verify_token = AsyncMock(
            side_effect=TokenExpiredError("Signature has expired")
        )
        client = TestClient(_make_app(agentid_verifier=verifier))

        resp = client.get("/whoami", headers={"Authorization": "Bearer expired-token"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "TOKEN_EXPIRED"

    def test_invalid_token_maps_to_invalid_token(self):
        verifier = AsyncMock()
        verifier.verify_token = AsyncMock(
            side_effect=TokenInvalidError("Malformed JWT")
        )
        client = TestClient(_make_app(agentid_verifier=verifier))

        resp = client.get("/whoami", headers={"Authorization": "Bearer malformed"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"

    def test_untrusted_provider_maps_to_invalid_token(self):
        verifier = AsyncMock()
        verifier.verify_token = AsyncMock(
            side_effect=ProviderUntrustedError(
                "Provider 'evil.example.com' is not trusted"
            )
        )
        client = TestClient(_make_app(agentid_verifier=verifier))

        resp = client.get(
            "/whoami", headers={"Authorization": "Bearer token-from-evil"}
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"

    def test_invalid_signature_maps_to_invalid_token(self):
        verifier = AsyncMock()
        verifier.verify_token = AsyncMock(
            side_effect=SignatureInvalidError("Signature verification failed")
        )
        client = TestClient(_make_app(agentid_verifier=verifier))

        resp = client.get("/whoami", headers={"Authorization": "Bearer tampered-token"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"
