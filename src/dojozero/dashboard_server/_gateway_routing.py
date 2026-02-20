"""Gateway routing for Dashboard Server.

Routes /api/gateway/{trial_id}/* requests to in-process trial gateways.
Enables external agents to connect to trials managed by the dashboard.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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

    Handles both regular and streaming responses.

    Args:
        app: Target FastAPI app
        request: Request to forward

    Returns:
        Response from the app
    """

    # For SSE endpoints, we need to handle streaming differently
    # Check if this is an SSE request
    accept_header = request.headers.get("accept", "")
    is_sse = "text/event-stream" in accept_header

    if is_sse:
        # Handle SSE streaming
        return await _forward_sse_request(app, request)

    # For non-streaming requests, use the ASGI interface directly
    response_started = False
    response_headers: list[tuple[bytes, bytes]] = []
    response_body: list[bytes] = []
    status_code = 200

    async def receive():
        body = await request.body()
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        nonlocal response_started, status_code, response_headers

        if message["type"] == "http.response.start":
            response_started = True
            status_code = message["status"]
            response_headers = message.get("headers", [])
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                response_body.append(body)

    # Call the app
    await app(request.scope, receive, send)

    # Build response
    headers = {k.decode(): v.decode() for k, v in response_headers if k and v}

    return Response(
        content=b"".join(response_body),
        status_code=status_code,
        headers=headers,
    )


async def _forward_sse_request(app: FastAPI, request: Request) -> StreamingResponse:
    """Forward an SSE request and return a streaming response.

    Args:
        app: Target FastAPI app
        request: SSE request to forward

    Returns:
        StreamingResponse that streams events
    """
    import asyncio

    # Queue for collecting SSE events
    event_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def receive():
        body = await request.body()
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            # We handle headers separately in StreamingResponse
            pass
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            more_body = message.get("more_body", False)
            if body:
                await event_queue.put(body)
            if not more_body:
                await event_queue.put(None)  # Signal end of stream

    async def stream_generator():
        # Start the app in a background task
        task = asyncio.create_task(app(request.scope, receive, send))

        try:
            while True:
                chunk = await event_queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = [
    "GatewayRouter",
    "create_gateway_routes",
]
