"""E2E pass 1 — discovery surface integration test.

Stands up a DojoZero gateway (with a real `HubPublisher` and a freshly
minted Ed25519 key) and an aip-activity app in the same process. Wires
the activity service's `HubManifestFetcher` to talk to the gateway via
`httpx.ASGITransport` so no network is involved. Then exercises the
discovery half of the chain:

    aip-activity HubManifestFetcher
        → GET /.well-known/agent-id-activity-manifest  (signed JWS)
        → GET /.well-known/agent-id-jwks               (verify signature)
        → GET /.well-known/agent-id-activity-categories
        → GET /.well-known/agent-id-activity-schemas/<verb>/<version>

The test passes only if the gateway's published surface validates
end-to-end against the real fetcher — catches drift between signer and
verifier, JWK encoding mismatches, schema URL drift, eTLD+1 enforcement.

Marked ``@pytest.mark.integration``; opt-in via ``pytest -m integration
--run-integration``. The test depends only on the SDK and the local
gateway code — no cross-repo dependency on aip-activity. (Earlier
versions imported ``app.schemas.namespace_ownership`` from aip-activity;
that helper now lives in agent-id-service-sdk.)

GitHub CI runs this freely now that the cross-repo dep is gone. Pass 2
(full ingest path with mocked IdP) would re-introduce the aip-activity
dep — deferred until there's a CI environment that can host it.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from agent_id_service_sdk import (
    HubManifestFetcher,
    NamespaceOwnershipError,
    generate_signing_keypair,
    verify_namespace_ownership,
)
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from fastapi.testclient import TestClient

from dojozero.gateway._hub_publisher import HubPublisher

pytestmark = pytest.mark.integration


SERVICE_ID = "https://api.dojozero.live"
NAMESPACE = "dojozero"
KID = "e2e-key-1"


@pytest.fixture
def hub_publisher():
    """Real HubPublisher with a freshly-minted Ed25519 key."""
    private_key, _public_jwk, pem = generate_signing_keypair(kid=KID)
    # Round-trip through PEM → load to mimic the production flow where
    # the key arrives via env var, not in-memory.
    loaded = load_pem_private_key(pem.encode("utf-8"), password=None)
    return HubPublisher(
        service_id=SERVICE_ID,
        namespace=NAMESPACE,
        private_key=loaded,  # type: ignore[arg-type]
        kid=KID,
    )


@pytest.fixture
def gateway_app(hub_publisher):
    """Minimal app exposing only the hub-discovery routes — same helper
    the dashboard server mounts in production. The ASGI transport in
    routed_async_client_factory drives this app; aip-activity's fetcher
    sees identical wire shape to a real deployment.
    """
    from fastapi import FastAPI

    from dojozero.gateway._hub_routes import register_hub_routes

    app = FastAPI()
    app.state.hub_publisher = hub_publisher
    register_hub_routes(app)
    return app


@pytest.fixture
def gateway_client(gateway_app):
    """A live TestClient over the gateway app — used both for direct
    asserts on the well-known routes and as the transport target for
    aip-activity's HubManifestFetcher."""
    with TestClient(gateway_app) as c:
        yield c


@pytest.fixture
def routed_async_client_factory(gateway_app, gateway_client):
    """Build a factory that returns an httpx.AsyncClient routing all
    requests for SERVICE_ID into the gateway app via ASGITransport.
    aip-activity's fetcher ``patch("httpx.AsyncClient", side_effect=...)``
    will use this so its discovery fetches stay in-process.

    Depends on ``gateway_client`` (not just ``gateway_app``) so the
    TestClient's lifespan is running for the duration of the test —
    otherwise ``app.state.gateway_state`` is never populated and every
    well-known route returns 503.

    We pin a reference to the real ``httpx.AsyncClient`` *before* the
    patch is installed; otherwise the factory recurses into its own
    patched stand-in.
    """
    del gateway_client  # only consumed for its lifespan side effect
    transport = httpx.ASGITransport(app=gateway_app)
    real_async_client = httpx.AsyncClient  # captured pre-patch

    def _factory(*_args, **_kwargs):
        # Always return a fresh client over the same transport; ignore
        # whatever timeout/headers the caller passes since we're
        # in-process.
        return real_async_client(transport=transport, base_url=SERVICE_ID)

    return _factory


# ---------------------------------------------------------------------------
# Direct surface checks — sanity that the gateway is actually serving.
# ---------------------------------------------------------------------------


class TestGatewaySurface:
    def test_jwks_doc_served(self, gateway_client):
        resp = gateway_client.get("/.well-known/agent-id-jwks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["keys"][0]["kty"] == "OKP"
        assert body["keys"][0]["crv"] == "Ed25519"
        assert body["keys"][0]["kid"] == KID

    def test_manifest_jws_served(self, gateway_client):
        resp = gateway_client.get("/.well-known/agent-id-activity-manifest")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/jose")
        assert resp.text.count(".") == 2

    def test_categories_served(self, gateway_client):
        resp = gateway_client.get("/.well-known/agent-id-activity-categories")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service_id"] == SERVICE_ID
        assert body["namespace"] == NAMESPACE
        cats = {c["category"] for c in body["categories"]}
        assert "dojozero.bet_decision" in cats

    def test_schema_served(self, gateway_client):
        resp = gateway_client.get(
            "/.well-known/agent-id-activity-schemas/bet_decision/1.0.0"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "object"
        assert "transaction_id" in body["required"]


# ---------------------------------------------------------------------------
# E2E: aip-activity's fetcher consumes the gateway's surface.
# ---------------------------------------------------------------------------


class TestFetcherConsumesGatewaySurface:
    """The discovery half of the full chain. Doesn't run aip-activity's
    ingest endpoint (that's pass 2) — proves the SDK fetcher accepts
    what HubPublisher serves, end-to-end through real httpx + JWS verify
    + JWKS verify + schema validation."""

    @pytest.mark.asyncio
    async def test_manifest_round_trip(self, routed_async_client_factory):
        fetcher = HubManifestFetcher()
        with patch("httpx.AsyncClient", side_effect=routed_async_client_factory):
            manifest = await fetcher.fetch(SERVICE_ID)

        assert manifest.service_id == SERVICE_ID
        assert manifest.namespace == NAMESPACE
        assert manifest.jwks_url.endswith("/.well-known/agent-id-jwks")
        assert manifest.categories_url.endswith(
            "/.well-known/agent-id-activity-categories"
        )

    @pytest.mark.asyncio
    async def test_categories_doc_round_trip(self, routed_async_client_factory):
        fetcher = HubManifestFetcher()
        with patch("httpx.AsyncClient", side_effect=routed_async_client_factory):
            doc = await fetcher.fetch_categories(SERVICE_ID)

        assert doc.service_id == SERVICE_ID
        assert doc.namespace == NAMESPACE
        entry = doc.find("dojozero.bet_decision")
        assert entry is not None
        assert entry.schema_version == "1.0.0"
        assert entry.schema_url.endswith(
            "/.well-known/agent-id-activity-schemas/bet_decision/1.0.0"
        )

    @pytest.mark.asyncio
    async def test_schema_round_trip(self, routed_async_client_factory):
        fetcher = HubManifestFetcher()
        with patch("httpx.AsyncClient", side_effect=routed_async_client_factory):
            schema = await fetcher.fetch_schema(
                SERVICE_ID, "dojozero.bet_decision", "1.0.0"
            )

        # Real JSON Schema, not a stub.
        assert schema["type"] == "object"
        assert "transaction_id" in schema["required"]
        # Decision-kind enum exactly matches the canonical Tier-2 schema.
        assert schema["properties"]["decision_kind"]["enum"] == [
            "moneyline",
            "spread",
            "over_under",
        ]


class TestNamespaceOwnership:
    """eTLD+1 ownership rule (design §6) catches namespace squatting on
    the verify side. The helper now lives in the SDK; same one any hub
    adopter's verifier would call after fetching the manifest."""

    def test_dojozero_passes_ownership(self):
        verify_namespace_ownership(SERVICE_ID, NAMESPACE)

    def test_cross_domain_squat_rejected(self):
        with pytest.raises(NamespaceOwnershipError):
            verify_namespace_ownership("https://api.evil.com", NAMESPACE)


class TestFetcherCachingAcrossCalls:
    """The fetcher's caches are stateful — verify subsequent calls don't
    re-fetch when warm. This catches accidental cache-invalidation bugs
    that would silently degrade prod performance."""

    @pytest.mark.asyncio
    async def test_manifest_cached_on_second_call(self, routed_async_client_factory):
        fetcher = HubManifestFetcher()
        with patch(
            "httpx.AsyncClient", side_effect=routed_async_client_factory
        ) as patched:
            await fetcher.fetch(SERVICE_ID)
            calls_after_first = patched.call_count
            await fetcher.fetch(SERVICE_ID)
            calls_after_second = patched.call_count

        # Second fetch should be a cache hit — no new client constructed.
        assert calls_after_second == calls_after_first
