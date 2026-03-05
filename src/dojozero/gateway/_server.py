"""Agent Gateway FastAPI server.

Provides HTTP API for external agents to participate in trials.
Follows patterns from dashboard_server/_server.py.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from dojozero.gateway._adapter import ExternalAgentAdapter
from dojozero.gateway._auth import AgentAuthenticator, NoOpAuthenticator
from dojozero.gateway._models import (
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    BalanceResponse,
    BetRequest,
    BetResponse,
    BetsListResponse,
    CurrentOddsResponse,
    ErrorCodes,
    ErrorDetail,
    ErrorResponse,
    EventEnvelope,
    RecentEventsResponse,
    TrialMetadataResponse,
    TrialResultsResponse,
)
from dojozero.gateway._sse import SSEConnection, create_sse_response

if TYPE_CHECKING:
    from dojozero.betting._broker import BrokerOperator
    from dojozero.data import DataHub

logger = logging.getLogger(__name__)


@dataclass
class GatewayState:
    """Shared state for the Gateway server."""

    trial_id: str
    data_hub: "DataHub"
    broker: "BrokerOperator"
    adapter: ExternalAgentAdapter
    authenticator: AgentAuthenticator = field(default_factory=NoOpAuthenticator)
    metadata: dict[str, Any] = field(default_factory=dict)


def get_gateway_state(request: Request) -> GatewayState:
    """Dependency to get gateway state from app.state."""
    state = getattr(request.app.state, "gateway_state", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Gateway not initialized")
    return state


def get_agent_id(
    x_agent_id: str | None = Header(default=None, alias="X-Agent-ID"),
    authorization: str | None = Header(default=None),
) -> str:
    """Extract agent ID from request headers.

    Phase 1-2: Simple X-Agent-ID header
    Phase 3: JWT token validation (TODO)

    Args:
        x_agent_id: Agent ID from X-Agent-ID header
        authorization: Bearer token (future JWT support)

    Returns:
        Agent ID string

    Raises:
        HTTPException: If no agent ID provided
    """
    if x_agent_id:
        return x_agent_id

    # TODO Phase 3: Extract from JWT
    if authorization and authorization.startswith("Bearer "):
        # For now, just require X-Agent-ID
        pass

    raise HTTPException(
        status_code=401,
        detail=ErrorResponse(
            error=ErrorDetail(
                code=ErrorCodes.AUTH_REQUIRED,
                message="X-Agent-ID header required",
            )
        ).model_dump(by_alias=True),
    )


def create_gateway_app(
    trial_id: str,
    data_hub: "DataHub",
    broker: "BrokerOperator",
    metadata: dict[str, Any] | None = None,
    authenticator: AgentAuthenticator | None = None,
) -> FastAPI:
    """Create the Agent Gateway FastAPI application.

    Args:
        trial_id: ID of the trial this gateway serves
        data_hub: DataHub instance for event subscriptions
        broker: BrokerOperator for betting operations
        metadata: Trial metadata
        authenticator: Optional authenticator for API key validation.
            If None, uses NoOpAuthenticator (allows any agent_id).

    Returns:
        FastAPI application
    """
    # Use NoOpAuthenticator if none provided (backwards compatible)
    auth = authenticator or NoOpAuthenticator()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage gateway lifecycle."""
        adapter = ExternalAgentAdapter(
            data_hub=data_hub,
            broker=broker,
            trial_id=trial_id,
        )

        state = GatewayState(
            trial_id=trial_id,
            data_hub=data_hub,
            broker=broker,
            adapter=adapter,
            authenticator=auth,
            metadata=metadata or {},
        )

        app.state.gateway_state = state
        auth_status = "enabled" if auth.is_enabled() else "disabled"
        logger.info("Gateway started for trial %s (auth: %s)", trial_id, auth_status)

        yield

        app.state.gateway_state = None
        logger.info("Gateway stopped for trial %s", trial_id)

    app = FastAPI(
        title="DojoZero Agent Gateway",
        description="HTTP API for external agents to participate in trials",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # =========================================================================
    # Registration Endpoints
    # =========================================================================

    @app.post("/agents", response_model=AgentRegistrationResponse)
    async def register_agent(
        request: AgentRegistrationRequest,
        state: GatewayState = Depends(get_gateway_state),
    ) -> AgentRegistrationResponse:
        """Register an external agent for this trial.

        API key is required. Agent identity (agent_id, display_name) is derived
        from the verified identity in agent_keys.yaml.

        Use 'dojo0 agents add' to register agents and get API keys.
        """
        # Convert initial_balance to string if it's a float
        initial_balance: str | None = None
        if request.initial_balance is not None:
            initial_balance = str(request.initial_balance)

        # Validate API key and get identity
        identity = await state.authenticator.validate(request.api_key)
        if identity is None:
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCodes.INVALID_TOKEN,
                        message="Invalid API key",
                    )
                ).model_dump(by_alias=True),
            )

        # Use verified identity - API key is the single source of truth
        # All identity/metadata comes from agent_keys.yaml
        logger.info(
            "Agent authenticated: api_key=***%s, agent_id=%s, persona=%s",
            request.api_key[-4:] if len(request.api_key) > 4 else "****",
            identity.agent_id,
            identity.persona or "(none)",
        )

        try:
            return await state.adapter.register_agent(
                agent_id=identity.agent_id,
                initial_balance=initial_balance,
                display_name=identity.display_name,
                persona=identity.persona,
                model=identity.model,
                model_display_name=identity.model_display_name,
                cdn_url=identity.cdn_url,
                authenticated=True,  # Always True - API key is required
            )
        except ValueError as e:
            error_msg = str(e)
            if "already" in error_msg.lower():
                raise HTTPException(
                    status_code=409,
                    detail=ErrorResponse(
                        error=ErrorDetail(
                            code=ErrorCodes.ALREADY_REGISTERED,
                            message=error_msg,
                        )
                    ).model_dump(by_alias=True),
                )
            raise HTTPException(status_code=400, detail=error_msg)

    @app.delete("/agents/{agent_id}")
    async def unregister_agent(
        agent_id: str,
        state: GatewayState = Depends(get_gateway_state),
    ) -> dict[str, str]:
        """Unregister an external agent."""
        if await state.adapter.unregister_agent(agent_id):
            return {"message": "Unregistered successfully"}
        raise HTTPException(status_code=404, detail="Agent not found")

    # =========================================================================
    # Trial Metadata
    # =========================================================================

    @app.get("/trial", response_model=TrialMetadataResponse)
    async def get_trial_metadata(
        state: GatewayState = Depends(get_gateway_state),
    ) -> TrialMetadataResponse:
        """Get trial metadata."""
        event = state.broker._event

        return TrialMetadataResponse(
            trial_id=state.trial_id,
            phase="running" if event and event.can_bet else "unknown",
            sport_type=state.metadata.get("sport_type", ""),
            game_id=event.event_id if event else "",
            home_team=event.home_team if event else "",
            away_team=event.away_team if event else "",
            game_time=event.game_time.isoformat()
            if event and event.game_time
            else None,
            metadata=state.metadata,
        )

    @app.get("/trial/results", response_model=TrialResultsResponse)
    async def get_trial_results(
        agent_id: str = Depends(get_agent_id),
        state: GatewayState = Depends(get_gateway_state),
    ) -> TrialResultsResponse:
        """Get current or final trial results.

        Returns the current standings during a trial, or final results after
        the trial has ended. This endpoint can be used to verify results
        if the trial_ended SSE event was missed.
        """
        if not state.adapter.is_registered(agent_id):
            raise HTTPException(
                status_code=403,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCodes.NOT_REGISTERED,
                        message="Agent not registered",
                    )
                ).model_dump(by_alias=True),
            )

        return await state.adapter.get_results()

    # =========================================================================
    # Event Streaming
    # =========================================================================

    @app.get("/events/stream")
    async def stream_events(
        request: Request,
        agent_id: str = Depends(get_agent_id),
        event_types: str | None = Query(
            default=None,
            description="Comma-separated event types to filter",
        ),
        state: GatewayState = Depends(get_gateway_state),
    ):
        """Stream events via SSE."""
        if not state.adapter.is_registered(agent_id):
            raise HTTPException(
                status_code=403,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCodes.NOT_REGISTERED,
                        message="Agent not registered",
                    )
                ).model_dump(by_alias=True),
            )

        # Parse event types filter
        filter_types = None
        if event_types:
            filter_types = [t.strip() for t in event_types.split(",")]

        # Get or create subscription
        subscription = await state.adapter.subscribe(
            agent_id=agent_id,
            event_types=filter_types,
            include_snapshot=True,
        )

        # Check for Last-Event-ID for reconnection
        last_event_id = request.headers.get("Last-Event-ID")

        # Create SSE connection with global sequence and event replay providers
        connection = SSEConnection(
            subscription=subscription,
            trial_id=state.trial_id,
            get_global_sequence=lambda: (
                state.data_hub.subscription_manager.global_sequence
            ),
            get_recent_events=lambda limit: state.data_hub.get_recent_events(
                limit=limit
            ),
            trial_ended_event=state.adapter.trial_ended_event,
            get_trial_ended_message=state.adapter.get_trial_ended_message,
        )

        return create_sse_response(connection, last_event_id)

    @app.get("/events/recent", response_model=RecentEventsResponse)
    async def get_recent_events(
        agent_id: str = Depends(get_agent_id),
        since: int | None = Query(
            default=None, description="Sequence number to get events since"
        ),
        limit: int = Query(default=50, le=100),
        event_types: str | None = Query(default=None),
        state: GatewayState = Depends(get_gateway_state),
    ) -> RecentEventsResponse:
        """Get recent events (polling fallback).

        When `since` is provided, only returns events with sequence > since.
        This allows efficient polling by requesting only new events.
        """
        if not state.adapter.is_registered(agent_id):
            raise HTTPException(status_code=403, detail="Agent not registered")

        # Parse event types filter
        filter_types = None
        if event_types:
            filter_types = [t.strip() for t in event_types.split(",")]

        # Get current sequence first
        current_sequence = state.data_hub.subscription_manager.global_sequence

        # If since >= current_sequence, no new events
        if since is not None and since >= current_sequence:
            return RecentEventsResponse(
                events=[],
                current_sequence=current_sequence,
            )

        # Get events from cache
        events = state.data_hub.get_recent_events(
            event_types=filter_types,
            limit=limit,
        )

        # Build response with envelopes, filtering by since
        envelopes = []
        for i, e in enumerate(events):
            # Events are newest-first, so sequence decreases with index
            event_sequence = current_sequence - i
            if since is not None and event_sequence <= since:
                # Skip events at or before the since sequence
                continue
            envelopes.append(
                EventEnvelope(
                    trial_id=state.trial_id,
                    sequence=event_sequence,
                    timestamp=e.timestamp,
                    payload=e.to_dict(),
                )
            )

        return RecentEventsResponse(
            events=envelopes,
            current_sequence=current_sequence,
        )

    # =========================================================================
    # Odds
    # =========================================================================

    @app.get("/odds/current", response_model=CurrentOddsResponse)
    async def get_current_odds(
        agent_id: str = Depends(get_agent_id),
        state: GatewayState = Depends(get_gateway_state),
    ) -> CurrentOddsResponse:
        """Get current betting odds."""
        if not state.adapter.is_registered(agent_id):
            raise HTTPException(status_code=403, detail="Agent not registered")

        return state.adapter.get_current_odds()

    # =========================================================================
    # Betting
    # =========================================================================

    @app.post("/bets", response_model=BetResponse)
    async def place_bet(
        request: BetRequest,
        agent_id: str = Depends(get_agent_id),
        state: GatewayState = Depends(get_gateway_state),
    ) -> BetResponse:
        """Place a bet."""
        try:
            return await state.adapter.place_bet(agent_id, request)
        except ValueError as e:
            error_str = str(e)

            if "stale" in error_str.lower():
                code = ErrorCodes.STALE_REFERENCE
                status = 400
            elif "balance" in error_str.lower():
                code = ErrorCodes.INSUFFICIENT_BALANCE
                status = 400
            elif "closed" in error_str.lower():
                code = ErrorCodes.BETTING_CLOSED
                status = 400
            elif "duplicate" in error_str.lower() or "idempotency" in error_str.lower():
                code = ErrorCodes.DUPLICATE_BET
                status = 409
            elif "not registered" in error_str.lower():
                code = ErrorCodes.NOT_REGISTERED
                status = 403
            else:
                code = ErrorCodes.BET_REJECTED
                status = 400

            raise HTTPException(
                status_code=status,
                detail=ErrorResponse(
                    error=ErrorDetail(code=code, message=error_str)
                ).model_dump(by_alias=True),
            )

    @app.get("/bets", response_model=BetsListResponse)
    async def get_bets(
        agent_id: str = Depends(get_agent_id),
        state: GatewayState = Depends(get_gateway_state),
    ) -> BetsListResponse:
        """Get all bets for the agent."""
        if not state.adapter.is_registered(agent_id):
            raise HTTPException(status_code=403, detail="Agent not registered")

        return BetsListResponse(bets=state.adapter.get_bets(agent_id))

    @app.get("/balance", response_model=BalanceResponse)
    async def get_balance(
        agent_id: str = Depends(get_agent_id),
        state: GatewayState = Depends(get_gateway_state),
    ) -> BalanceResponse:
        """Get agent's balance and holdings."""
        try:
            return state.adapter.get_balance(agent_id)
        except ValueError as e:
            error_str = str(e)
            if "not registered" in error_str.lower():
                raise HTTPException(status_code=403, detail="Agent not registered")
            raise HTTPException(status_code=404, detail=error_str)

    # =========================================================================
    # Agent List
    # =========================================================================

    @app.get("/agents")
    async def list_agents(
        state: GatewayState = Depends(get_gateway_state),
    ) -> dict[str, Any]:
        """List all registered external agents."""
        agents = []
        for agent_id, agent_state in state.adapter._agents.items():
            # Get balance from broker if account exists
            balance = None
            if agent_id in state.broker._accounts:
                balance = str(state.broker._accounts[agent_id].balance)

            agents.append(
                {
                    "agent_id": agent_id,
                    "registered_at": agent_state.registered_at.isoformat(),
                    "last_activity_at": agent_state.last_activity_at.isoformat()
                    if agent_state.last_activity_at
                    else None,
                    "balance": balance,
                }
            )
        return {
            "agents": agents,
            "count": len(agents),
        }

    # =========================================================================
    # Leaderboard / All Agents Statistics
    # =========================================================================

    @app.get("/leaderboard")
    async def get_leaderboard(
        state: GatewayState = Depends(get_gateway_state),
    ) -> dict[str, Any]:
        """Get leaderboard showing all agents' balances and statistics.

        Includes both internal AI agents and external agents.
        """
        leaderboard = []

        # Get all accounts from broker
        for agent_id, account in state.broker._accounts.items():
            # Get statistics for this agent
            stats = await state.broker.get_statistics(agent_id)

            # Check if this is an external agent
            is_external = agent_id in state.adapter._agents

            leaderboard.append(
                {
                    "agent_id": agent_id,
                    "is_external": is_external,
                    "balance": str(account.balance),
                    "total_bets": stats.total_bets,
                    "total_wagered": str(stats.total_wagered),
                    "wins": stats.wins,
                    "losses": stats.losses,
                    "win_rate": round(stats.win_rate, 4),
                    "net_profit": str(stats.net_profit),
                    "roi": round(stats.roi, 4),
                }
            )

        # Sort by balance descending
        leaderboard.sort(key=lambda x: float(x["balance"]), reverse=True)

        return {
            "trial_id": state.trial_id,
            "leaderboard": leaderboard,
            "total_agents": len(leaderboard),
            "external_agents": sum(1 for a in leaderboard if a["is_external"]),
            "internal_agents": sum(1 for a in leaderboard if not a["is_external"]),
        }

    # =========================================================================
    # Health Check
    # =========================================================================

    @app.get("/health")
    async def health_check(
        state: GatewayState = Depends(get_gateway_state),
    ) -> dict[str, Any]:
        """Health check endpoint."""
        return {
            "status": "ok",
            "trial_id": state.trial_id,
            "registered_agents": len(state.adapter._agents),
            "betting_open": state.broker._event.can_bet
            if state.broker._event
            else False,
        }

    return app


__all__ = [
    "GatewayState",
    "create_gateway_app",
    "get_agent_id",
    "get_gateway_state",
]
