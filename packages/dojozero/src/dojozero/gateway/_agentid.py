"""AgentID integration for the Gateway.

Thin wrapper around `agent-id-service-sdk`. The library does the actual JWKS
caching, JWT verification, key-rotation handling, and activity-event reporting
— this module exists only to read the gateway's env config and return a
configured `Verifier`, while keeping the import lazy so the gateway runs
without the optional dep installed.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_id_service_sdk import Verifier

logger = logging.getLogger(__name__)


def agentid_verifier_from_env() -> "Verifier | None":
    """Build a `Verifier` from environment variables.

    Returns ``None`` if AgentID auth is not configured. Both
    ``DOJOZERO_AGENTID_TRUSTED_PROVIDERS`` and ``DOJOZERO_AGENTID_AUDIENCE`` must be
    set to enable.

    Raises:
        RuntimeError: AgentID is configured but ``agent-id-service-sdk`` is not
            installed. Install with ``pip install dojozero[agentid]``.

    Required env vars (when enabled):
        ``DOJOZERO_AGENTID_TRUSTED_PROVIDERS``: comma-separated list of trusted
            IdP domains, e.g. ``"pre.agent-id.live"``.
        ``DOJOZERO_AGENTID_AUDIENCE``: expected ``aud`` claim — the gateway's
            public origin, e.g. ``"https://api.dojozero.live"``.

    Optional env vars:
        ``DOJOZERO_AGENTID_CACHE_TTL_SECONDS``: JWKS cache TTL (default ``3600``).
        ``DOJOZERO_AGENTID_CLOCK_SKEW_SECONDS``: clock-skew tolerance (default ``30``).
        ``DOJOZERO_AGENTID_PROVIDER_URLS``: JSON object mapping provider domain to
            base URL, used to override the default ``https://<domain>`` lookup
            for local-dev / non-https IdPs.
        ``DOJOZERO_AGENTID_ACTIVITY_API_KEY``: API key for posting events to the
            activity service. When set together with ``DOJOZERO_AGENTID_AGENT_TOKEN``,
            the verifier auto-emits ``auth.verify`` events on every successful
            token check, and the gateway emits ``session.start``/``session.end``
            at agent register / shutdown.
        ``DOJOZERO_AGENTID_AGENT_TOKEN``: the gateway's own AgentID management token.
            Forwarded as ``X-AIP-Token`` to the activity service so the
            principal-policy claim can be verified.
        ``DOJOZERO_AGENTID_ACTIVITY_ENDPOINT``: override for the activity-service
            POST URL. Normally resolved via the IdP's discovery doc; set this
            only when discovery is unreachable.
        ``DOJOZERO_AGENTID_SERVICE_NAME``: ``service`` envelope field on emitted
            events. Defaults to ``"dojozero-gateway"``.
    """
    trusted_raw = os.environ.get("DOJOZERO_AGENTID_TRUSTED_PROVIDERS", "").strip()
    audience = os.environ.get("DOJOZERO_AGENTID_AUDIENCE", "").strip()

    if not trusted_raw and not audience:
        return None

    if not trusted_raw or not audience:
        logger.warning(
            "AgentID partially configured (trusted_providers=%r, audience=%r); "
            "both DOJOZERO_AGENTID_TRUSTED_PROVIDERS and DOJOZERO_AGENTID_AUDIENCE "
            "must be set — AgentID auth disabled",
            bool(trusted_raw),
            bool(audience),
        )
        return None

    trusted = [p.strip() for p in trusted_raw.split(",") if p.strip()]
    if not trusted:
        return None

    try:
        from agent_id_service_sdk import Verifier
    except ImportError as exc:
        raise RuntimeError(
            "AgentID authentication configured (DOJOZERO_AGENTID_TRUSTED_PROVIDERS / "
            "DOJOZERO_AGENTID_AUDIENCE) but agent-id-service-sdk is not installed. "
            "Install with: pip install dojozero[agentid]"
        ) from exc

    cache_ttl = int(os.environ.get("DOJOZERO_AGENTID_CACHE_TTL_SECONDS", "3600"))
    clock_skew = int(os.environ.get("DOJOZERO_AGENTID_CLOCK_SKEW_SECONDS", "30"))

    provider_urls: dict[str, str] = {}
    provider_urls_raw = os.environ.get("DOJOZERO_AGENTID_PROVIDER_URLS", "").strip()
    if provider_urls_raw:
        provider_urls = json.loads(provider_urls_raw)

    agent_token = os.environ.get("DOJOZERO_AGENTID_AGENT_TOKEN") or None
    activity_endpoint = os.environ.get("DOJOZERO_AGENTID_ACTIVITY_ENDPOINT") or None
    service_name = os.environ.get("DOJOZERO_AGENTID_SERVICE_NAME") or "dojozero-gateway"

    # HubJWS envelope auth (design §5.0): the gateway signs each activity
    # POST with the same Ed25519 key it uses for manifest signing. Reuse
    # HubPublisher's loaded key so we don't double-load the PEM.
    hub_signing_key = None
    hub_signing_kid = None
    hub_service_id = None
    try:
        from dojozero.gateway._hub_publisher import HubPublisher  # noqa: PLC0415

        publisher = HubPublisher.from_env()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "hub publisher load failed (%s); activity emission disabled", exc
        )
        publisher = None
    if publisher is not None:
        hub_signing_key = publisher._private_key  # type: ignore[attr-defined]
        hub_signing_kid = publisher.kid
        hub_service_id = publisher.service_id

    verifier = Verifier(
        trusted_providers=trusted,
        audience=audience,
        cache_ttl=cache_ttl,
        clock_skew_seconds=clock_skew,
        provider_urls=provider_urls,
        activity_endpoint=activity_endpoint,
        service_name=service_name,
        # Per-request auth.verify auto-emit is OFF on purpose. At full
        # DojoZero load the SDK's auto-emit produces millions of
        # near-identical events per day; we hand-emit Tier-1 categories
        # with real signal (auth.deny, session.start, session.end) from
        # ``gateway/_activity.py`` instead. See that module's docstring
        # for the first-adopter rationale.
        report_auto_verify=False,
        agent_token_for_emit=agent_token,
        hub_signing_key=hub_signing_key,
        hub_signing_kid=hub_signing_kid,
        hub_service_id=hub_service_id,
    )

    logger.info(
        "AgentID verifier configured: trusted_providers=%s, audience=%s, "
        "cache_ttl=%ds, clock_skew=%ds, provider_urls=%s, activity=%s",
        trusted,
        audience,
        cache_ttl,
        clock_skew,
        provider_urls or "(none)",
        "enabled" if hub_signing_key else "disabled",
    )
    return verifier


__all__ = ["agentid_verifier_from_env"]
