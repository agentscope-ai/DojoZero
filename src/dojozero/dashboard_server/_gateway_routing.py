"""Gateway routing for Dashboard Server.

Routes /gw/{trial_id}/* requests to in-process trial gateways.
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

# Header name for agent ID
AGENT_ID_HEADER = "X-Agent-ID"


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
        GET /gw - List all trial gateways
        ANY /gw/{trial_id}/{path:path} - Route to trial gateway

    Args:
        router: GatewayRouter instance

    Returns:
        FastAPI app that can be mounted on the main dashboard app
    """

    app = FastAPI(
        title="Gateway Router",
        description="Routes requests to per-trial gateways",
    )

    @app.get("/gw")
    async def list_gateways() -> dict[str, Any]:
        """List all available trial gateways."""
        trial_ids = router.list_gateways()
        return {
            "gateways": [
                {"trial_id": tid, "endpoint": f"/gw/{tid}"} for tid in trial_ids
            ],
            "count": len(trial_ids),
        }

    @app.api_route(
        "/gw/{trial_id}/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def route_to_gateway(
        request: Request,
        trial_id: str,
        path: str,
    ) -> Response:
        """Route request to the appropriate trial gateway.

        The path is rewritten from /gw/{trial_id}/...
        to /... for the trial's gateway.
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

        # Check if this is an SSE request to the events stream endpoint
        accept_header = request.headers.get("accept", "")
        is_sse = "text/event-stream" in accept_header
        is_events_stream = path == "events/stream"

        if is_sse and is_events_stream:
            # Handle SSE directly without httpx (httpx doesn't handle async streaming well)
            gateway_state = router.get_gateway_state(trial_id)
            if gateway_state is None:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "Gateway state not found",
                        }
                    },
                )
            return await _handle_sse_directly(request, gateway_state)

        # Rewrite the path to remove /gw/{trial_id} prefix
        # Original: /gw/{trial_id}/events/stream
        # Rewritten: /events/stream
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

        # Route through the gateway app (non-SSE requests)
        response = await _forward_to_app(gateway, new_request)
        return response

    return app


async def _handle_sse_directly(
    request: Request,
    gateway_state: "GatewayState",
) -> StreamingResponse:
    """Handle SSE events stream directly without httpx.

    This bypasses httpx.ASGITransport which doesn't properly handle
    async generators that block on asyncio operations.

    Args:
        request: The incoming request
        gateway_state: Gateway state with adapter and data_hub

    Returns:
        StreamingResponse with SSE events
    """
    from dojozero.gateway._sse import SSEConnection, create_sse_response

    # Get agent ID from header
    agent_id = request.headers.get(AGENT_ID_HEADER)
    if not agent_id:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "X-Agent-ID header required",
                }
            },
        )

    # Check if agent is registered
    if not gateway_state.adapter.is_registered(agent_id):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {"code": "NOT_REGISTERED", "message": "Agent not registered"}
            },
        )

    # Parse event types filter from query params
    event_types_param = request.query_params.get("event_types")
    filter_types = None
    if event_types_param:
        filter_types = [t.strip() for t in event_types_param.split(",")]

    # Get or create subscription
    subscription = await gateway_state.adapter.subscribe(
        agent_id=agent_id,
        event_types=filter_types,
        include_snapshot=True,
    )

    # Check for Last-Event-ID for reconnection
    last_event_id = request.headers.get("Last-Event-ID")

    # Create SSE connection with global sequence and event replay providers
    connection = SSEConnection(
        subscription=subscription,
        trial_id=gateway_state.trial_id,
        get_global_sequence=lambda: (
            gateway_state.data_hub.subscription_manager.global_sequence
        ),
        get_recent_events=lambda limit: gateway_state.data_hub.get_recent_events(
            limit=limit
        ),
        trial_ended_event=gateway_state.adapter.trial_ended_event,
        get_trial_ended_message=gateway_state.adapter.get_trial_ended_message,
    )

    logger.info(
        "SSE stream started via direct proxy: trial=%s, agent=%s",
        gateway_state.trial_id,
        agent_id,
    )

    return create_sse_response(connection, last_event_id)


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
