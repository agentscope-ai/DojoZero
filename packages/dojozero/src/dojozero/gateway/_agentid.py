"""ModelScope AgentID verification for the Gateway (Hub side).

A thin wrapper around ``agent-id-service-sdk``'s ``Verifier``. It builds a
ModelScope-aligned verifier from environment config; the gateway uses it to
authenticate ``Authorization: Bearer`` JWTs issued by the ModelScope Agent IdP
and to derive the caller's ``agent_id`` (the token's ``sub``).

Scope: token verification only. Activity reporting and approvals are
deliberately out of scope here (deferred) — ModelScope's IdP exposes neither,
so this module does the one thing the migration needs day-one.

The ``agent-id-service-sdk`` import is lazy (inside the builder) so the gateway
runs without the optional dependency installed; importing this module is always
safe.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from fastapi import HTTPException

from dojozero.gateway._models import ErrorCodes, ErrorDetail, ErrorResponse

if TYPE_CHECKING:
    from agent_id_service_sdk import Verifier  # pyright: ignore[reportMissingImports]

logger = logging.getLogger(__name__)


def _json_env(name: str) -> dict | None:
    """Parse a JSON-object env var, or return None (warn on malformed)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("%s is not valid JSON; ignoring", name)
        return None
    return value if isinstance(value, dict) else None


def agentid_verifier_from_env() -> "Verifier | None":
    """Build a ModelScope-aligned ``Verifier`` from env, or None if unconfigured.

    Returns ``None`` (AgentID auth disabled) unless both of these are set:

      ``DOJOZERO_AGENTID_TRUSTED_PROVIDERS``
          Comma-separated issuer domains to trust, e.g. ``"pre.modelscope.cn"``
          (prod: ``"www.modelscope.cn"``). Matched against the JWT ``iss`` host.
      ``DOJOZERO_AGENTID_AUDIENCE``
          The gateway's **registered hub client_id** (e.g. ``"hub_4abb08"``) —
          ModelScope issues ``aud`` as the client_id, NOT an origin URL.

    Optional:
      ``DOJOZERO_AGENTID_JWKS_URLS``
          JSON map ``domain -> exact JWKS URL``. For ModelScope, set this to
          bypass discovery, e.g.
          ``{"pre.modelscope.cn": "https://pre.modelscope.cn/openapi/v1/agent_id/.well-known/agentid-jwks"}``.
      ``DOJOZERO_AGENTID_PROVIDER_URLS``
          JSON map ``domain -> base URL`` (discovery override; local dev).
      ``DOJOZERO_AGENTID_CACHE_TTL_SECONDS`` (default 3600)
      ``DOJOZERO_AGENTID_CLOCK_SKEW_SECONDS`` (default 30)

    If AgentID is configured but ``agent-id-service-sdk`` is not installed, logs
    a warning and returns ``None`` (verification disabled) — an optional feature
    requires its optional install (``pip install dojozero[agentid]``).
    """
    trusted_raw = os.environ.get("DOJOZERO_AGENTID_TRUSTED_PROVIDERS", "").strip()
    audience = os.environ.get("DOJOZERO_AGENTID_AUDIENCE", "").strip()

    if not trusted_raw and not audience:
        return None
    if not trusted_raw or not audience:
        logger.warning(
            "AgentID partially configured (trusted_providers=%r, audience=%r); both "
            "DOJOZERO_AGENTID_TRUSTED_PROVIDERS and DOJOZERO_AGENTID_AUDIENCE must be "
            "set — AgentID auth disabled",
            bool(trusted_raw),
            bool(audience),
        )
        return None

    trusted = [p.strip() for p in trusted_raw.split(",") if p.strip()]
    if not trusted:
        return None

    try:
        from agent_id_service_sdk import Verifier  # pyright: ignore[reportMissingImports]
    except ImportError:
        logger.warning(
            "AgentID auth is configured (DOJOZERO_AGENTID_TRUSTED_PROVIDERS / "
            "DOJOZERO_AGENTID_AUDIENCE) but agent-id-service-sdk is not installed "
            "— AgentID verification DISABLED. Install: pip install dojozero[agentid]"
        )
        return None

    cache_ttl = int(os.environ.get("DOJOZERO_AGENTID_CACHE_TTL_SECONDS", "3600"))
    clock_skew = int(os.environ.get("DOJOZERO_AGENTID_CLOCK_SKEW_SECONDS", "30"))
    jwks_urls = _json_env("DOJOZERO_AGENTID_JWKS_URLS")
    provider_urls = _json_env("DOJOZERO_AGENTID_PROVIDER_URLS")

    verifier = Verifier(
        trusted_providers=trusted,
        audience=audience,
        cache_ttl=cache_ttl,
        clock_skew_seconds=clock_skew,
        provider_urls=provider_urls or None,
        jwks_urls=jwks_urls or None,
        # ModelScope tokens carry no cnf.jkt — DPoP is never used on this path.
        dpop_mode="disabled",
    )
    logger.info(
        "AgentID verifier configured: trusted_providers=%s, audience=%s, jwks_urls=%s",
        trusted,
        audience,
        "set" if jwks_urls else "(discovery)",
    )
    return verifier


async def verify_bearer(verifier, authorization: str | None):
    """Verify an ``Authorization: Bearer <jwt>`` header via the AgentID verifier.

    Returns the ``VerifiedAgent``. Raises ``HTTPException(401)`` when the header
    is missing / not a Bearer token, or verification fails. Callers guarantee
    ``verifier is not None``. Single source of truth for the gateway's Bearer
    auth so the per-request and registration paths can't drift.
    """
    if not (authorization and authorization.startswith("Bearer ")):
        raise HTTPException(
            status_code=401,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCodes.AUTH_REQUIRED,
                    message="AgentID Bearer token required",
                )
            ).model_dump(by_alias=True),
        )
    try:
        return await verifier.verify(authorization)
    except Exception as exc:  # noqa: BLE001 — any verify failure → 401
        raise HTTPException(
            status_code=401,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCodes.INVALID_TOKEN,
                    message=f"AgentID token verification failed: {exc}",
                )
            ).model_dump(by_alias=True),
        )


__all__ = ["agentid_verifier_from_env", "verify_bearer"]
