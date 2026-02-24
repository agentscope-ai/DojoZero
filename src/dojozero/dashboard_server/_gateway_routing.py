"""Gateway routing for Dashboard Server.

Routes /api/gateway/{trial_id}/* requests to in-process trial gateways.
Enables external agents to connect to trials managed by the dashboard.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from dojozero.gateway._server import GatewayState

logger = logging.getLogger(__name__)


class GatewayRouter:
    """Routes requests to per-trial gateway apps.

    Manages a registry of trial gateways and routes incoming requests
    to the appropriate trial based on the trial_id path parameter.
    """

    def __init__(self) -> None:
        """Initialize the gateway router."""
        self._gateways: dict[str, FastAPI] = {}
        self._gateway_states: dict[str, "GatewayState"] = {}

    def register_gateway(
        self,
        trial_id: str,
        gateway_app: FastAPI,
        gateway_state: "GatewayState",
    ) -> None:
        """Register a trial's gateway for routing.

        Args:
            trial_id: Trial identifier
            gateway_app: FastAPI app for the trial's gateway
            gateway_state: Gateway state for direct access
        """
        self._gateways[trial_id] = gateway_app
        self._gateway_states[trial_id] = gateway_state
        logger.info("Registered gateway for trial: %s", trial_id)

    def unregister_gateway(self, trial_id: str) -> bool:
        """Unregister a trial's gateway.

        Args:
            trial_id: Trial identifier

        Returns:
            True if gateway was unregistered, False if not found
        """
        if trial_id in self._gateways:
            del self._gateways[trial_id]
            del self._gateway_states[trial_id]
            logger.info("Unregistered gateway for trial: %s", trial_id)
            return True
        return False

    def get_gateway(self, trial_id: str) -> FastAPI | None:
        """Get gateway app for a trial.

        Args:
            trial_id: Trial identifier

        Returns:
            Gateway FastAPI app or None if not found
        """
        return self._gateways.get(trial_id)

    def get_gateway_state(self, trial_id: str) -> "GatewayState | None":
        """Get gateway state for a trial.

        Args:
            trial_id: Trial identifier

        Returns:
            GatewayState or None if not found
        """
        return self._gateway_states.get(trial_id)

    def list_gateways(self) -> list[str]:
        """List all registered trial IDs with gateways.

        Returns:
            List of trial IDs
        """
        return list(self._gateways.keys())


def create_gateway_routes(router: GatewayRouter) -> FastAPI:
    """Create a FastAPI sub-application for gateway routing.

    Routes:
        GET /api/gateway - List all trial gateways
        ANY /api/gateway/{trial_id}/{path:path} - Route to trial gateway

    Args:
        router: GatewayRouter instance

    Returns:
        FastAPI app that can be mounted on the main dashboard app
    """

    app = FastAPI(
        title="Gateway Router",
        description="Routes requests to per-trial gateways",
    )

    @app.get("/api/gateway")
    async def list_gateways() -> dict[str, Any]:
        """List all available trial gateways."""
        trial_ids = router.list_gateways()
        return {
            "gateways": [
                {"trial_id": tid, "endpoint": f"/api/gateway/{tid}"}
                for tid in trial_ids
            ],
            "count": len(trial_ids),
        }

    @app.api_route(
        "/api/gateway/{trial_id}/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def route_to_gateway(
        request: Request,
        trial_id: str,
        path: str,
    ) -> Response:
        """Route request to the appropriate trial gateway.

        The path is rewritten from /api/gateway/{trial_id}/api/v1/...
        to /api/v1/... for the trial's gateway.
        """
        gateway = router.get_gateway(trial_id)
        if gateway is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {
                        "code": "TRIAL_NOT_FOUND",
                        "message": f"No gateway found for trial: {trial_id}",
                    }
                },
            )

        # Rewrite the path to remove /api/gateway/{trial_id} prefix
        # Original: /api/gateway/{trial_id}/api/v1/events/stream
        # Rewritten: /api/v1/events/stream
        new_path = f"/{path}" if path else "/"

        # Create a new scope with the rewritten path
        scope = dict(request.scope)
        scope["path"] = new_path
        scope["raw_path"] = new_path.encode()

        # Remove path_params that were consumed
        if "path_params" in scope:
            scope["path_params"] = {}

        # Create a new request with modified scope
        new_request = Request(scope, request.receive, request._send)

        # Route through the gateway app
        response = await _forward_to_app(gateway, new_request)
        return response

    return app


async def _forward_to_app(app: FastAPI, request: Request) -> Response:
    """Forward a request to a FastAPI app and return the response.

    Uses httpx.ASGITransport for clean request forwarding.

    Args:
        app: Target FastAPI app
        request: Request to forward

    Returns:
        Response from the app
    """
    # Build path with query string
    path = request.scope["path"]
    if request.query_params:
        path = f"{path}?{request.query_params}"

    # Prepare headers (remove host to avoid conflicts)
    headers = dict(request.headers)
    headers.pop("host", None)

    body = await request.body()

    # Check if this is an SSE request
    accept_header = request.headers.get("accept", "")
    is_sse = "text/event-stream" in accept_header

    if is_sse:
        # For SSE, stream the response
        async def stream_sse():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
                base_url="http://internal",
            ) as client:
                async with client.stream(
                    method=request.method,
                    url=path,
                    headers=headers,
                    content=body if body else None,
                ) as response:
                    async for chunk in response.aiter_bytes():
                        yield chunk

        return StreamingResponse(
            stream_sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # For regular requests, forward and return response
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://internal",
    ) as client:
        response = await client.request(
            method=request.method,
            url=path,
            headers=headers,
            content=body if body else None,
        )

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
        )


__all__ = [
    "GatewayRouter",
    "create_gateway_routes",
]
