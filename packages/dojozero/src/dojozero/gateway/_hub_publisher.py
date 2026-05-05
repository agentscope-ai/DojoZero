"""Hub publisher: serves DojoZero's `.well-known/*` discovery surface.

DojoZero is a first-adopter hub of the AgentID activity protocol. Per the
decentralized discovery design (`agent-identity/design/2026-05-04-activity-discovery.en.md`):

- ``/.well-known/agent-id-jwks`` — manifest-signing public key(s)
- ``/.well-known/agent-id-activity-manifest`` — JWS-signed manifest
- ``/.well-known/agent-id-activity-categories`` — Tier-2 catalog
- ``/.well-known/agent-id-activity-schemas/<verb>/<version>`` — JSON Schema

All four are public; ``aip-activity`` fetches and verifies them. The hub
identity is distinct from the agent JWTs the gateway authenticates: those
are signed by the IdP and verified here, while the manifest is signed
*by* the gateway and verified by ``aip-activity``.

Environment:
    ``DOJOZERO_HUB_SERVICE_ID`` — required, e.g. ``https://api.dojozero.live``.
    ``DOJOZERO_HUB_NAMESPACE`` — Tier-2 prefix (default ``dojozero``).
    ``DOJOZERO_HUB_SIGNING_KEY_PEM`` — Ed25519 private key in PEM, e.g.
        injected from a k8s secret. Without it, the publisher is disabled
        and the well-known routes return 503.
    ``DOJOZERO_HUB_SIGNING_KID`` — key id (default ``hub-key-1``).
"""

from __future__ import annotations

import logging
import os
from base64 import urlsafe_b64encode
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import load_pem_private_key

logger = logging.getLogger(__name__)


# --- Categories + schemas ---------------------------------------------------
#
# DojoZero's first Tier-2 category. Sits alongside the Tier-1
# ``transfer.value`` event when a bet is placed: same ``transaction_id``
# joins the two. Tier-2 is the hub-flavored richness — sport, model,
# decision kind — which the canonical Tier-1 transfer doesn't carry.

_BET_DECISION_V1: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "dojozero.bet_decision v1.0.0",
    "type": "object",
    "required": ["transaction_id", "decision_kind"],
    "additionalProperties": False,
    "properties": {
        # Joins the Tier-1 transfer.value event.
        "transaction_id": {"type": "string", "minLength": 1},
        "decision_kind": {
            "type": "string",
            "enum": ["moneyline", "spread", "over_under"],
        },
        "selection": {"type": "string"},
        # Privacy-preserving stake size; same buckets as Tier-1.
        "stake_bucket": {
            "type": "string",
            "enum": ["lt_100", "100_1k", "1k_10k", "10k_plus"],
        },
        "confidence_bucket": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        # sha256:<hex> of the agent's natural-language reasoning. Raw text
        # never leaves the agent — this is provenance, not the rationale.
        "rationale_hash": {
            "type": "string",
            "pattern": "^sha256:[0-9a-f]{64}$",
        },
        "model": {"type": "string"},
        "game_id": {"type": "string"},
        "sport": {"type": "string"},
    },
}


# (category, schema_version) → (schema dict, sensitive_fields, deprecated)
_CATALOG: dict[tuple[str, str], dict[str, Any]] = {
    ("dojozero.bet_decision", "1.0.0"): {
        "schema": _BET_DECISION_V1,
        "sensitive_fields": (),
        "deprecated": False,
    },
}


# --- Publisher --------------------------------------------------------------


class HubPublisher:
    """Holds the hub identity + signed-discovery state.

    Construct via :meth:`from_env`. Once initialised the published
    artefacts are derived deterministically from the configured
    ``service_id``/``namespace``/key — only ``signed_manifest()`` does
    real work (JWS signing) and the result is memoised.
    """

    def __init__(
        self,
        *,
        service_id: str,
        namespace: str,
        private_key: Ed25519PrivateKey,
        kid: str,
    ) -> None:
        self.service_id = service_id.rstrip("/")
        self.namespace = namespace
        self._private_key = private_key
        self._public_key = private_key.public_key()
        self.kid = kid
        self._signed_manifest_cache: str | None = None

    # -- discovery URLs (kept in one place so routes + manifest agree) --

    @property
    def jwks_url(self) -> str:
        return f"{self.service_id}/.well-known/agent-id-jwks"

    @property
    def categories_url(self) -> str:
        return f"{self.service_id}/.well-known/agent-id-activity-categories"

    def schema_url(self, category: str, version: str) -> str:
        verb = category.split(".", 1)[1]
        return (
            f"{self.service_id}/.well-known/agent-id-activity-schemas/{verb}/{version}"
        )

    # -- artefacts ------------------------------------------------------

    def jwks_doc(self) -> dict[str, Any]:
        return {"keys": [_public_key_to_jwk(self._public_key, self.kid)]}

    def signed_manifest(self) -> str:
        # Memoise: the inputs are immutable for the publisher's lifetime,
        # and JWS signatures are deterministic for EdDSA over the same
        # payload (RFC 8032). Cache to avoid re-signing on every fetch.
        cached = self._signed_manifest_cache
        if cached is not None:
            return cached
        from agent_id_service_sdk import build_manifest, sign_manifest

        manifest = build_manifest(
            service_id=self.service_id,
            namespace=self.namespace,
            categories_url=self.categories_url,
            jwks_url=self.jwks_url,
        )
        signed = sign_manifest(
            manifest,
            private_key=self._private_key,
            kid=self.kid,
        )
        self._signed_manifest_cache = signed
        return signed

    def categories_doc(self) -> dict[str, Any]:
        entries = []
        for (category, version), meta in _CATALOG.items():
            if not category.startswith(f"{self.namespace}."):
                # Catalog entry doesn't belong to this hub's namespace —
                # skip rather than serve a categories doc the activity
                # service will reject (eTLD+1 enforces ownership).
                continue
            entries.append(
                {
                    "category": category,
                    "schema_version": version,
                    "schema_url": self.schema_url(category, version),
                    "schema_format": "json-schema",
                    "deprecated": meta["deprecated"],
                    "sensitive_fields": list(meta["sensitive_fields"]),
                }
            )
        return {
            "service_id": self.service_id,
            "namespace": self.namespace,
            "categories": entries,
        }

    def schema(self, category: str, version: str) -> dict[str, Any] | None:
        meta = _CATALOG.get((category, version))
        return meta["schema"] if meta else None

    # -- factory --------------------------------------------------------

    @classmethod
    def from_env(cls) -> "HubPublisher | None":
        """Return a publisher built from ``DOJOZERO_HUB_*`` env vars, or
        ``None`` if not configured. A misconfigured key (present but
        unparseable) raises so deployments fail loudly.
        """
        service_id = os.environ.get("DOJOZERO_HUB_SERVICE_ID", "").strip()
        key_pem = os.environ.get("DOJOZERO_HUB_SIGNING_KEY_PEM", "").strip()

        if not service_id or not key_pem:
            if service_id or key_pem:
                logger.warning(
                    "Hub publisher partially configured "
                    "(DOJOZERO_HUB_SERVICE_ID=%s, DOJOZERO_HUB_SIGNING_KEY_PEM=%s); "
                    "both are required — publisher disabled",
                    bool(service_id),
                    bool(key_pem),
                )
            return None

        namespace = os.environ.get("DOJOZERO_HUB_NAMESPACE", "dojozero").strip()
        kid = os.environ.get("DOJOZERO_HUB_SIGNING_KID", "hub-key-1").strip()

        loaded = load_pem_private_key(key_pem.encode("utf-8"), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise RuntimeError(
                "DOJOZERO_HUB_SIGNING_KEY_PEM must be an Ed25519 private key "
                f"(got {type(loaded).__name__})"
            )

        publisher = cls(
            service_id=service_id,
            namespace=namespace,
            private_key=loaded,
            kid=kid,
        )
        logger.info(
            "Hub publisher configured: service_id=%s, namespace=%s, kid=%s",
            publisher.service_id,
            publisher.namespace,
            publisher.kid,
        )
        return publisher


def _public_key_to_jwk(public_key: Ed25519PublicKey, kid: str) -> dict[str, str]:
    """Encode an Ed25519 public key as a JWK (RFC 8037)."""
    raw = public_key.public_bytes_raw()
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": kid,
        "x": urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
    }


__all__ = ["HubPublisher"]
