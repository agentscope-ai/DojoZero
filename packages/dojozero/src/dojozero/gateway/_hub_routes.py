"""Public ``.well-known/*`` hub-discovery routes (design §3 / §5.0).

Hub identity is process-wide and origin-wide — not trial-scoped. The
four routes here describe DojoZero-the-hub: its JWKS, its signed
manifest, its categories catalog, and its per-category JSON schemas.
They live on whichever long-lived FastAPI app serves the public origin
(in production: the dashboard server).

Mount with :func:`register_hub_routes`; the helper reads
``app.state.hub_publisher`` at request time so callers can populate it
during their own lifespan setup.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse


def register_hub_routes(app: FastAPI) -> None:
    """Register the four ``.well-known/*`` hub-discovery routes on ``app``.

    The caller is responsible for setting ``app.state.hub_publisher``
    during its lifespan startup. When ``hub_publisher`` is ``None`` (env
    not configured), every route returns 503 with a config-hint message.
    """

    def _require_publisher(request: Request):
        pub = getattr(request.app.state, "hub_publisher", None)
        if pub is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "hub publisher is not configured "
                    "(DOJOZERO_HUB_SERVICE_ID + DOJOZERO_HUB_SIGNING_KEY_PEM)"
                ),
            )
        return pub

    @app.get("/.well-known/agent-id-jwks")
    async def well_known_jwks(request: Request) -> dict[str, Any]:
        return _require_publisher(request).jwks_doc()

    @app.get("/.well-known/agent-id-activity-manifest")
    async def well_known_manifest(request: Request):
        jws = _require_publisher(request).signed_manifest()
        return PlainTextResponse(jws, media_type="application/jose")

    @app.get("/.well-known/agent-id-activity-categories")
    async def well_known_categories(request: Request) -> dict[str, Any]:
        return _require_publisher(request).categories_doc()

    @app.get("/.well-known/agent-id-activity-schemas/{verb}/{version}")
    async def well_known_schema(
        verb: str, version: str, request: Request
    ) -> dict[str, Any]:
        pub = _require_publisher(request)
        schema = pub.schema(f"{pub.namespace}.{verb}", version)
        if schema is None:
            raise HTTPException(
                status_code=404,
                detail=f"no schema for category {verb!r} version {version!r}",
            )
        return schema


__all__ = ["register_hub_routes"]
