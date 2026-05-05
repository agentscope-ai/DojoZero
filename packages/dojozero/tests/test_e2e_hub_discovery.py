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
--run-integration`` because it requires ``aip-activity`` installed in
the venv (it's not a runtime dep of DojoZero, so it's not in the dev
group). To enable:

    uv pip install -e ../aip-activity

GitHub CI doesn't run this — DojoZero is on github.com but aip-activity
lives on Alibaba's internal GitLab, so cross-repo checkout isn't
available. Aone is the planned CI home where both repos are reachable.
For now: contributors touching the discovery surface run this locally
before merging.

Pass 2 (full ingest path with mocked IdP) is a separate test, deferred.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

# aip-activity isn't a hard dep of DojoZero; skip if not installed.
pytest.importorskip("app.main")
pytest.importorskip("app.routes.activity")

from agent_id_service_sdk import (  # noqa: E402
    HubManifestFetcher,
    generate_signing_keypair,
)
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    load_pem_private_key,
)
from fastapi.testclient import TestClient  # noqa: E402

from dojozero.gateway._hub_publisher import HubPublisher  # noqa: E402
from dojozero.gateway._server import create_gateway_app  # noqa: E402

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
    data_hub = MagicMock()
    broker = MagicMock()
    broker._event = None
    broker._accounts = {}
    return create_gateway_app(
        trial_id="trial-e2e",
        data_hub=data_hub,
        broker=broker,
        metadata={},
        hub_publisher=hub_publisher,
    )


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
    the verify side. We exercise this by giving aip-activity's namespace
    check the manifest's actual service_id and asserting it accepts
    *dojozero* but would reject a hub at a different registrable domain."""

    def test_dojozero_passes_ownership(self):
        # Imported lazily — the module-level pytest.importorskip gates
        # whether this whole module runs. Pyright can't see that runtime
        # gate, so silence its missing-import complaint here.
        from app.schemas.namespace_ownership import (  # type: ignore[import-not-found]  # noqa: PLC0415
            verify_namespace_ownership,
        )

        # Real check — same one aip-activity runs after fetcher.fetch().
        verify_namespace_ownership(SERVICE_ID, NAMESPACE)

    def test_cross_domain_squat_rejected(self):
        from app.schemas.namespace_ownership import (  # type: ignore[import-not-found]  # noqa: PLC0415
            NamespaceOwnershipError,
            verify_namespace_ownership,
        )

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
