"""Tests for the gateway's `.well-known/*` hub-discovery routes.

Covers:
- JWKS doc shape (kty/crv/kid/x for Ed25519)
- Manifest is JWS-signed; verifying with the published JWKS round-trips
  back to the canonical claim set
- Categories doc lists `dojozero.bet_decision` and points at the schema URL
- Schema route returns the JSON Schema; unknown verb/version → 404
- All routes return 503 when DOJOZERO_HUB_* env is not set

Round-trip via HubManifestFetcher proves wire compatibility with
aip-activity's discovery client.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from agent_id_service_sdk import HubManifestFetcher
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from fastapi.testclient import TestClient

from dojozero.gateway._hub_publisher import HubPublisher

SERVICE_ID = "https://api.dojozero.live"
NAMESPACE = "dojozero"
KID = "hub-key-1"


def _generate_key_pem() -> tuple[Ed25519PrivateKey, str]:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    ).decode("utf-8")
    return key, pem


@pytest.fixture
def keypair():
    key, pem = _generate_key_pem()
    return key, pem


@pytest.fixture
def publisher(keypair):
    key, _ = keypair
    return HubPublisher(
        service_id=SERVICE_ID,
        namespace=NAMESPACE,
        private_key=key,
        kid=KID,
    )


def _hub_only_app(publisher):
    """Minimal FastAPI app exercising only the hub-discovery routes.

    These routes live on the dashboard server in production, not on
    per-trial gateways. The helper exposed by ``_hub_routes`` is what
    both code paths use; tests just mount it on a bare app.
    """
    from fastapi import FastAPI

    from dojozero.gateway._hub_routes import register_hub_routes

    app = FastAPI()
    app.state.hub_publisher = publisher
    register_hub_routes(app)
    return app


@pytest.fixture
def client_with_publisher(publisher):
    with TestClient(_hub_only_app(publisher)) as c:
        yield c


@pytest.fixture
def client_without_publisher():
    with TestClient(_hub_only_app(None)) as c:
        yield c


class TestJWKS:
    def test_returns_ed25519_okp_key(self, client_with_publisher):
        resp = client_with_publisher.get("/.well-known/agent-id-jwks")
        assert resp.status_code == 200
        body = resp.json()
        assert "keys" in body
        assert len(body["keys"]) == 1
        key = body["keys"][0]
        assert key["kty"] == "OKP"
        assert key["crv"] == "Ed25519"
        assert key["kid"] == KID
        assert key["x"]  # base64url-encoded raw public key


class TestManifest:
    def test_returns_compact_jws(self, client_with_publisher):
        resp = client_with_publisher.get("/.well-known/agent-id-activity-manifest")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/jose")
        # Compact JWS: <header>.<payload>.<signature>
        assert resp.text.count(".") == 2

    def test_manifest_payload_carries_canonical_fields(
        self, client_with_publisher, keypair
    ):
        key, _ = keypair
        resp = client_with_publisher.get("/.well-known/agent-id-activity-manifest")
        # Verify directly with the public key — proves the gateway signed
        # with the kid published in JWKS.
        claims = jwt.decode(
            resp.text,
            key.public_key(),
            algorithms=["EdDSA"],
            options={"verify_aud": False, "verify_exp": False},
        )
        assert claims["service_id"] == SERVICE_ID
        assert claims["namespace"] == NAMESPACE
        assert claims["categories_url"].endswith(
            "/.well-known/agent-id-activity-categories"
        )
        assert claims["jwks_url"].endswith("/.well-known/agent-id-jwks")
        assert claims["aip_version"] == "0.1"


class TestCategories:
    def test_lists_bet_decision(self, client_with_publisher):
        resp = client_with_publisher.get("/.well-known/agent-id-activity-categories")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service_id"] == SERVICE_ID
        assert body["namespace"] == NAMESPACE
        cats = body["categories"]
        assert any(
            c["category"] == "dojozero.bet_decision"
            and c["schema_version"] == "1.0.0"
            and c["schema_format"] == "json-schema"
            and c["schema_url"].endswith(
                "/.well-known/agent-id-activity-schemas/bet_decision/1.0.0"
            )
            for c in cats
        )


class TestSchema:
    def test_returns_json_schema_for_known_category(self, client_with_publisher):
        resp = client_with_publisher.get(
            "/.well-known/agent-id-activity-schemas/bet_decision/1.0.0"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "object"
        assert "transaction_id" in body["required"]
        assert body["properties"]["decision_kind"]["enum"] == [
            "moneyline",
            "spread",
            "over_under",
        ]

    def test_unknown_verb_returns_404(self, client_with_publisher):
        resp = client_with_publisher.get(
            "/.well-known/agent-id-activity-schemas/no_such_verb/1.0.0"
        )
        assert resp.status_code == 404

    def test_unknown_version_returns_404(self, client_with_publisher):
        resp = client_with_publisher.get(
            "/.well-known/agent-id-activity-schemas/bet_decision/9.9.9"
        )
        assert resp.status_code == 404


class TestPublisherDisabled:
    def test_jwks_503_when_no_publisher(self, client_without_publisher):
        resp = client_without_publisher.get("/.well-known/agent-id-jwks")
        assert resp.status_code == 503

    def test_manifest_503_when_no_publisher(self, client_without_publisher):
        resp = client_without_publisher.get("/.well-known/agent-id-activity-manifest")
        assert resp.status_code == 503

    def test_categories_503_when_no_publisher(self, client_without_publisher):
        resp = client_without_publisher.get("/.well-known/agent-id-activity-categories")
        assert resp.status_code == 503

    def test_schema_503_when_no_publisher(self, client_without_publisher):
        resp = client_without_publisher.get(
            "/.well-known/agent-id-activity-schemas/bet_decision/1.0.0"
        )
        assert resp.status_code == 503


class TestRoundTrip:
    """Wire-compat test: HubManifestFetcher resolves the gateway's
    well-known surface to the same claim set we put in. If this passes,
    aip-activity will accept the gateway's manifest unchanged."""

    @pytest.mark.asyncio
    async def test_fetcher_round_trip(self, client_with_publisher, publisher):
        # Patch httpx.AsyncClient so the fetcher hits our TestClient
        # synchronously instead of making real HTTP calls.
        manifest_jws = client_with_publisher.get(
            "/.well-known/agent-id-activity-manifest"
        ).text
        jwks_doc = client_with_publisher.get("/.well-known/agent-id-jwks").json()
        categories_doc = client_with_publisher.get(
            "/.well-known/agent-id-activity-categories"
        ).json()
        schema_doc = client_with_publisher.get(
            "/.well-known/agent-id-activity-schemas/bet_decision/1.0.0"
        ).json()

        def _build_client(*_args, **_kwargs):
            mock = AsyncMock()

            async def _aenter(*_a, **_kw):
                return mock

            async def _aexit(*_a, **_kw):
                return None

            mock.__aenter__ = _aenter
            mock.__aexit__ = _aexit

            async def _get(url, *_a, **_kw):
                resp = AsyncMock()
                resp.status_code = 200
                resp.raise_for_status = lambda: None
                if url.endswith("/agent-id-activity-manifest"):
                    resp.text = manifest_jws
                elif url.endswith("/agent-id-jwks"):
                    resp.json = lambda: jwks_doc
                elif url.endswith("/agent-id-activity-categories"):
                    resp.json = lambda: categories_doc
                elif url.endswith("/bet_decision/1.0.0"):
                    resp.json = lambda: schema_doc
                return resp

            mock.get = _get
            return mock

        fetcher = HubManifestFetcher()
        with patch("httpx.AsyncClient", side_effect=_build_client):
            verified = await fetcher.fetch(SERVICE_ID)
            cats = await fetcher.fetch_categories(SERVICE_ID)
            schema = await fetcher.fetch_schema(
                SERVICE_ID, "dojozero.bet_decision", "1.0.0"
            )

        assert verified.service_id == SERVICE_ID
        assert verified.namespace == NAMESPACE
        entry = cats.find("dojozero.bet_decision")
        assert entry is not None
        assert entry.schema_version == "1.0.0"
        assert schema["type"] == "object"


class TestFromEnv:
    def test_returns_none_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            assert HubPublisher.from_env() is None

    def test_returns_publisher_when_configured(self, keypair):
        _, pem = keypair
        env = {
            "DOJOZERO_HUB_SERVICE_ID": SERVICE_ID,
            "DOJOZERO_HUB_NAMESPACE": NAMESPACE,
            "DOJOZERO_HUB_SIGNING_KEY_PEM": pem,
            "DOJOZERO_HUB_SIGNING_KID": KID,
        }
        with patch.dict(os.environ, env, clear=True):
            pub = HubPublisher.from_env()
        assert pub is not None
        assert pub.service_id == SERVICE_ID
        assert pub.namespace == NAMESPACE
        assert pub.kid == KID

    def test_partial_config_returns_none(self):
        # Only service_id, no key — disabled with a warning, not an error.
        with patch.dict(
            os.environ,
            {"DOJOZERO_HUB_SERVICE_ID": SERVICE_ID},
            clear=True,
        ):
            assert HubPublisher.from_env() is None

    def test_non_ed25519_key_raises(self):
        from cryptography.hazmat.primitives.asymmetric.rsa import (
            generate_private_key,
        )

        rsa_key = generate_private_key(public_exponent=65537, key_size=2048)
        pem = rsa_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        ).decode("utf-8")
        env = {
            "DOJOZERO_HUB_SERVICE_ID": SERVICE_ID,
            "DOJOZERO_HUB_SIGNING_KEY_PEM": pem,
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="Ed25519"):
                HubPublisher.from_env()
