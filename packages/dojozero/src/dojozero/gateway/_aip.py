"""AIP (Agent Identity Protocol) integration for the Gateway.

Thin wrapper around `aip-identity-verify`. The library does the actual JWKS
caching, JWT verification, and key-rotation handling — this module exists only
to read the gateway's env config and return a configured `AIPVerifier`, while
keeping the import lazy so the gateway runs without the optional dep installed.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aip_identity_verify import AIPVerifier

logger = logging.getLogger(__name__)


def aip_verifier_from_env() -> "AIPVerifier | None":
    """Build an `AIPVerifier` from environment variables.

    Returns ``None`` if AIP auth is not configured. Both
    ``DOJOZERO_AIP_TRUSTED_PROVIDERS`` and ``DOJOZERO_AIP_AUDIENCE`` must be
    set to enable.

    Raises:
        RuntimeError: AIP is configured but ``aip-identity-verify`` is not
            installed. Install with ``pip install dojozero[aip]``.

    Required env vars (when enabled):
        ``DOJOZERO_AIP_TRUSTED_PROVIDERS``: comma-separated list of trusted
            IdP domains, e.g. ``"pre.agent-id.live"``.
        ``DOJOZERO_AIP_AUDIENCE``: expected ``aud`` claim — the gateway's
            public origin, e.g. ``"https://api.dojozero.live"``.

    Optional env vars:
        ``DOJOZERO_AIP_CACHE_TTL_SECONDS``: JWKS cache TTL (default ``3600``).
        ``DOJOZERO_AIP_CLOCK_SKEW_SECONDS``: clock-skew tolerance (default ``30``).
        ``DOJOZERO_AIP_PROVIDER_URLS``: JSON object mapping provider domain to
            base URL, used to override the default ``https://<domain>`` lookup
            for local-dev / non-https IdPs.
    """
    trusted_raw = os.environ.get("DOJOZERO_AIP_TRUSTED_PROVIDERS", "").strip()
    audience = os.environ.get("DOJOZERO_AIP_AUDIENCE", "").strip()

    if not trusted_raw and not audience:
        return None

    if not trusted_raw or not audience:
        logger.warning(
            "AIP partially configured (trusted_providers=%r, audience=%r); "
            "both DOJOZERO_AIP_TRUSTED_PROVIDERS and DOJOZERO_AIP_AUDIENCE "
            "must be set — AIP auth disabled",
            bool(trusted_raw),
            bool(audience),
        )
        return None

    trusted = [p.strip() for p in trusted_raw.split(",") if p.strip()]
    if not trusted:
        return None

    try:
        from aip_identity_verify import AIPVerifier
    except ImportError as exc:
        raise RuntimeError(
            "AIP authentication configured (DOJOZERO_AIP_TRUSTED_PROVIDERS / "
            "DOJOZERO_AIP_AUDIENCE) but aip-identity-verify is not installed. "
            "Install with: pip install dojozero[aip]"
        ) from exc

    cache_ttl = int(os.environ.get("DOJOZERO_AIP_CACHE_TTL_SECONDS", "3600"))
    clock_skew = int(os.environ.get("DOJOZERO_AIP_CLOCK_SKEW_SECONDS", "30"))

    provider_urls: dict[str, str] = {}
    provider_urls_raw = os.environ.get("DOJOZERO_AIP_PROVIDER_URLS", "").strip()
    if provider_urls_raw:
        provider_urls = json.loads(provider_urls_raw)

    verifier = AIPVerifier(
        trusted_providers=trusted,
        audience=audience,
        cache_ttl=cache_ttl,
        clock_skew_seconds=clock_skew,
        provider_urls=provider_urls,
    )

    logger.info(
        "AIP verifier configured: trusted_providers=%s, audience=%s, "
        "cache_ttl=%ds, clock_skew=%ds, provider_urls=%s",
        trusted,
        audience,
        cache_ttl,
        clock_skew,
        provider_urls or "(none)",
    )
    return verifier


__all__ = ["aip_verifier_from_env"]
