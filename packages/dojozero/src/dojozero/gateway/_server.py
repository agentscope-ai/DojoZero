"""Agent Gateway FastAPI server.

Provides HTTP API for external agents to participate in trials.
Follows patterns from dashboard_server/_server.py.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from dojozero.gateway._adapter import ExternalAgentAdapter
from dojozero.gateway._agentid import agentid_verifier_from_env, verify_bearer
from dojozero.gateway._auth import AgentAuthenticator, NoOpAuthenticator
from dojozero.gateway._models import (
    AgentReconnectRequest,
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    AgentUnregisterRequest,
    BalanceResponse,
    BetRequest,
    BetResponse,
    BetsListResponse,
    CurrentOddsResponse,
    ErrorCodes,
    ErrorDetail,
    ErrorResponse,
    EventEnvelope,
    EventInfoResponse,
    PredictionRequest,
    PredictionResponse,
    PredictionsListResponse,
    RecentEventsResponse,
    TrialEndedInfo,
    TrialMetadataResponse,
    TrialResultsResponse,
)
from dojozero.gateway._sse import SSEConnection, create_sse_response

if TYPE_CHECKING:
    from dojozero.betting._protocol import ContestOperator
    from dojozero.data import DataHub

logger = logging.getLogger(__name__)


@dataclass
class GatewayState:
    """Shared state for the Gateway server."""

    trial_id: str
    data_hub: "DataHub"
    broker: "ContestOperator"
    adapter: ExternalAgentAdapter
    authenticator: AgentAuthenticator = field(default_factory=NoOpAuthenticator)
    # Optional ModelScope AgentID verifier (agent_id_service_sdk.Verifier).
    # When set, Bearer JWTs are cryptographically verified and the token's
    # ``sub`` is the authoritative agent_id. ``Any`` to avoid importing the
    # optional SDK at module load.
    agentid_verifier: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


def get_gateway_state(request: Request) -> GatewayState:
    """Dependency to get gateway state from app.state."""
    state = getattr(request.app.state, "gateway_state", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Gateway not initialized")
    return state


async def get_agent_id(
    x_agent_id: str | None = Header(default=None, alias="X-Agent-ID"),
    authorization: str | None = Header(default=None),
    state: GatewayState = Depends(get_gateway_state),
) -> str:
    """Resolve the caller's agent_id for per-request authorization.

    When a ModelScope AgentID verifier is configured, a cryptographically
    verified ``Authorization: Bearer`` token is REQUIRED and its ``sub`` is the
    identity — the unverified ``X-Agent-ID`` header is NOT accepted (honoring it
    would let any caller impersonate a registered agent). When no verifier is
    configured, the legacy ``X-Agent-ID`` header is used (dev / pre-AgentID).

    Raises:
        HTTPException: 401 when the required token is missing/invalid, or no
            identity is provided.
    """
    verifier = state.agentid_verifier
    if verifier is not None:
        # AgentID enabled: a verified Bearer token is the ONLY accepted identity.
        # verify_bearer rejects a missing/non-Bearer header, so the unverified
        # X-Agent-ID header can't be used to impersonate a registered agent.
        verified = await verify_bearer(verifier, authorization)
        return verified.agent_id

    # No verifier configured → legacy X-Agent-ID header auth.
    if x_agent_id:
        return x_agent_id

    raise HTTPException(
        status_code=401,
        detail=ErrorResponse(
            error=ErrorDetail(
                code=ErrorCodes.AUTH_REQUIRED,
                message="Authentication required: provide an X-Agent-ID header",
            )
        ).model_dump(by_alias=True),
    )


def create_gateway_app(
    trial_id: str,
    data_hub: "DataHub",
    broker: "ContestOperator",
    metadata: dict[str, Any] | None = None,
    authenticator: AgentAuthenticator | None = None,
    agentid_verifier: Any = None,
) -> FastAPI:
    """Create the Agent Gateway FastAPI application.

    Args:
        trial_id: ID of the trial this gateway serves
        data_hub: DataHub instance for event subscriptions
        broker: Any :class:`ContestOperator` (classic betting, prediction, or
            a future contest type). The gateway dispatches HTTP routes based
            on ``broker.get_contest_kind()`` and uses ``isinstance`` narrows
            only for mode-specific operations.
        metadata: Trial metadata
        authenticator: Optional authenticator for API key validation.
            If None, uses NoOpAuthenticator (allows any agent_id).

    Returns:
        FastAPI application
    """
    # Use NoOpAuthenticator if none provided (backwards compatible)
    auth = authenticator or NoOpAuthenticator()

    # AgentID verifier: use the injected one, else build from env. The builder
    # returns None when AgentID isn't configured or the optional SDK isn't
    # installed, so this is safe (and a no-op) when unconfigured.
    verifier = (
        agentid_verifier
        if agentid_verifier is not None
        else agentid_verifier_from_env()
    )

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
            agentid_verifier=verifier,
            metadata=metadata or {},
        )

        app.state.gateway_state = state
        auth_status = "enabled" if auth.is_enabled() else "disabled"
        agentid_status = "enabled" if verifier is not None else "disabled"
        logger.info(
            "Gateway started for trial %s (api_key auth: %s, agentid: %s)",
            trial_id,
            auth_status,
            agentid_status,
        )

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
        authorization: str | None = Header(default=None),
        state: GatewayState = Depends(get_gateway_state),
    ) -> AgentRegistrationResponse:
        """Register an external agent for this trial.

        Identity comes from either a verified AgentID Bearer token (a ModelScope
        JWT whose ``sub`` is the agent_id) or, failing that, a validated API key
        whose identity is defined in agent_keys.yaml.
        """
        # Convert initial_balance to string if it's a float
        initial_balance: str | None = None
        if request.initial_balance is not None:
            initial_balance = str(request.initial_balance)

        # AgentID path: identity from the verified ModelScope JWT.
        verifier = state.agentid_verifier
        if (
            verifier is not None
            and authorization
            and authorization.startswith("Bearer ")
        ):
            verified = await verify_bearer(verifier, authorization)
            logger.info(
                "Agent authenticated via AgentID: agent_id=%s", verified.agent_id
            )
            try:
                return await state.adapter.register_agent(
                    agent_id=verified.agent_id,
                    initial_balance=initial_balance,
                    display_name=verified.agent_name or None,
                    authenticated=True,
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

    @app.post("/agents/reconnect", response_model=AgentRegistrationResponse)
    async def reconnect_agent(
        request: AgentReconnectRequest,
        authorization: str | None = Header(default=None),
        state: GatewayState = Depends(get_gateway_state),
    ) -> AgentRegistrationResponse:
        """Reconnect an existing agent using its identity + session key.

        Identity comes from either a verified AgentID Bearer token (a ModelScope
        JWT) or a validated API key. The session key (returned during original
        registration) proves ownership of the session.
        """
        # AgentID path: identity from the verified ModelScope JWT.
        verifier = state.agentid_verifier
        if (
            verifier is not None
            and authorization
            and authorization.startswith("Bearer ")
        ):
            verified = await verify_bearer(verifier, authorization)
            try:
                return await state.adapter.reconnect_agent(
                    agent_id=verified.agent_id,
                    session_key=request.session_key,
                    display_name=verified.agent_name or None,
                )
            except ValueError as e:
                error_msg = str(e)
                if "not registered" in error_msg.lower():
                    raise HTTPException(
                        status_code=404,
                        detail=ErrorResponse(
                            error=ErrorDetail(
                                code=ErrorCodes.NOT_REGISTERED,
                                message=error_msg,
                            )
                        ).model_dump(by_alias=True),
                    )
                if "session key" in error_msg.lower():
                    raise HTTPException(
                        status_code=403,
                        detail=ErrorResponse(
                            error=ErrorDetail(
                                code=ErrorCodes.SESSION_KEY_INVALID,
                                message=error_msg,
                            )
                        ).model_dump(by_alias=True),
                    )
                raise HTTPException(status_code=400, detail=error_msg)

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

        try:
            return await state.adapter.reconnect_agent(
                agent_id=identity.agent_id,
                session_key=request.session_key,
                display_name=identity.display_name,
                persona=identity.persona,
                model=identity.model,
                model_display_name=identity.model_display_name,
                cdn_url=identity.cdn_url,
            )
        except ValueError as e:
            error_msg = str(e)
            if "not registered" in error_msg.lower():
                raise HTTPException(
                    status_code=404,
                    detail=ErrorResponse(
                        error=ErrorDetail(
                            code=ErrorCodes.NOT_REGISTERED,
                            message=error_msg,
                        )
                    ).model_dump(by_alias=True),
                )
            if "session key" in error_msg.lower():
                raise HTTPException(
                    status_code=403,
                    detail=ErrorResponse(
                        error=ErrorDetail(
                            code=ErrorCodes.SESSION_KEY_INVALID,
                            message=error_msg,
                        )
                    ).model_dump(by_alias=True),
                )
            raise HTTPException(status_code=400, detail=error_msg)

    @app.delete("/agents/{agent_id}")
    async def unregister_agent(
        agent_id: str,
        request: AgentUnregisterRequest,
        state: GatewayState = Depends(get_gateway_state),
    ) -> dict[str, str]:
        """Unregister an external agent.

        Requires the session key (returned during registration) to prove ownership.
        """
        try:
            if await state.adapter.unregister_agent(agent_id, request.session_key):
                return {"message": "Unregistered successfully"}
            raise HTTPException(status_code=404, detail="Agent not found")
        except ValueError as e:
            error_msg = str(e)
            if "session key" in error_msg.lower():
                raise HTTPException(
                    status_code=403,
                    detail=ErrorResponse(
                        error=ErrorDetail(
                            code=ErrorCodes.SESSION_KEY_INVALID,
                            message=error_msg,
                        )
                    ).model_dump(by_alias=True),
                )
            raise HTTPException(status_code=400, detail=error_msg)

    # =========================================================================
    # Trial Metadata
    # =========================================================================

    @app.get("/trial", response_model=TrialMetadataResponse)
    async def get_trial_metadata(
        state: GatewayState = Depends(get_gateway_state),
    ) -> TrialMetadataResponse:
        """Get trial metadata."""
        event = state.broker.current_event
        can_bet = getattr(event, "can_bet", False) if event else False

        return TrialMetadataResponse(
            trial_id=state.trial_id,
            phase="running" if event and can_bet else "unknown",
            sport_type=state.metadata.get("sport_type", ""),
            game_id=event.event_id if event else "",
            home_team=event.home_team if event else "",
            away_team=event.away_team if event else "",
            game_time=event.game_time.isoformat()
            if event and event.game_time
            else None,
            metadata=state.metadata,
        )

    @app.get("/rules")
    async def get_rules(
        state: GatewayState = Depends(get_gateway_state),
    ) -> dict[str, Any]:
        """Return contest rules for this trial.

        Every broker implements ``get_rules()`` via the ContestOperator
        protocol, so no mode check is needed.
        """
        return cast(dict[str, Any], state.broker.get_rules())

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
        limit: int = Query(default=100, le=100),
        state: GatewayState = Depends(get_gateway_state),
    ) -> RecentEventsResponse:
        """Get recent events via polling with pagination.

        Returns events with sequence > since, ordered oldest-first.
        Client should keep polling while len(events) == limit to catch up.
        """
        if not state.adapter.is_registered(agent_id):
            raise HTTPException(status_code=403, detail="Agent not registered")

        current_sequence = state.data_hub.subscription_manager.global_sequence

        # Get events from sequential cache with real sequence numbers
        since_seq = since if since is not None else 0
        sequenced_events = state.data_hub.get_events_since(since=since_seq, limit=limit)

        # Build response with real sequences
        envelopes = [
            EventEnvelope(
                trial_id=state.trial_id,
                sequence=seq,
                timestamp=event.timestamp,
                payload=event.to_dict(),
            )
            for seq, event in sequenced_events
        ]

        # Check if trial has ended
        trial_ended_info = None
        ended_msg = state.adapter.get_trial_ended_message()
        if ended_msg is not None:
            trial_ended_info = TrialEndedInfo(
                reason=ended_msg.reason,
                message=ended_msg.message,
            )

        return RecentEventsResponse(
            events=envelopes,
            current_sequence=current_sequence,
            trial_ended=trial_ended_info,
        )

    # =========================================================================
    # Per-contest routes
    #
    # Each contest type owns its set of routes; the gateway only registers
    # the ones that apply to the active broker. Routes that don't apply
    # simply don't exist on the FastAPI app — clients get a clean 404 from
    # the router instead of a body-only "mode mismatch" hidden behind a
    # generic 4xx code, and adding a third contest type later is purely
    # additive instead of compounding `is_prediction_mode` branches.
    # =========================================================================

    # Resolve once at app-build time. The broker is fixed for the gateway's
    # lifetime — we never hot-swap contest types on a live app — so the
    # static dispatch is intentional.
    contest_kind = broker.get_contest_kind()

    if contest_kind == "classic_betting":

        @app.get("/odds/current", response_model=CurrentOddsResponse)
        async def get_current_odds(
            agent_id: str = Depends(get_agent_id),
            state: GatewayState = Depends(get_gateway_state),
        ) -> CurrentOddsResponse:
            """Get current betting odds (classic betting only)."""
            if not state.adapter.is_registered(agent_id):
                raise HTTPException(status_code=403, detail="Agent not registered")
            return state.adapter.get_current_odds()

        @app.post("/bets", response_model=BetResponse)
        async def place_bet(
            request: BetRequest,
            agent_id: str = Depends(get_agent_id),
            state: GatewayState = Depends(get_gateway_state),
        ) -> BetResponse:
            """Place a bet (classic betting only)."""
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
                elif (
                    "duplicate" in error_str.lower()
                    or "idempotency" in error_str.lower()
                ):
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
            """Get all bets for the agent (classic betting only)."""
            if not state.adapter.is_registered(agent_id):
                raise HTTPException(status_code=403, detail="Agent not registered")
            return BetsListResponse(bets=state.adapter.get_bets(agent_id))

        @app.get("/balance", response_model=BalanceResponse)
        async def get_balance(
            agent_id: str = Depends(get_agent_id),
            state: GatewayState = Depends(get_gateway_state),
        ) -> BalanceResponse:
            """Get agent's balance and holdings (classic betting only)."""
            try:
                return state.adapter.get_balance(agent_id)
            except ValueError as e:
                error_str = str(e)
                if "not registered" in error_str.lower():
                    raise HTTPException(status_code=403, detail="Agent not registered")
                raise HTTPException(status_code=404, detail=error_str)

    elif contest_kind == "window_pool_prediction":

        @app.post("/predictions", response_model=PredictionResponse)
        async def submit_prediction(
            request: PredictionRequest,
            agent_id: str = Depends(get_agent_id),
            state: GatewayState = Depends(get_gateway_state),
        ) -> PredictionResponse:
            """Submit a prediction (prediction mode only)."""
            try:
                return await state.adapter.submit_prediction(
                    agent_id, request.selection
                )
            except ValueError as e:
                error_str = str(e)
                lower = error_str.lower()
                if "not registered" in lower:
                    code = ErrorCodes.NOT_REGISTERED
                    status = 403
                elif (
                    "closed" in lower or "not accepting" in lower or "settled" in lower
                ):
                    # "settled" covers the broker's "Event already settled"
                    # path, which is morally CLOSED for clients.
                    code = ErrorCodes.PREDICTION_CLOSED
                    status = 400
                else:
                    code = ErrorCodes.PREDICTION_REJECTED
                    status = 400

                raise HTTPException(
                    status_code=status,
                    detail=ErrorResponse(
                        error=ErrorDetail(code=code, message=error_str)
                    ).model_dump(by_alias=True),
                )

        @app.get("/predictions", response_model=PredictionsListResponse)
        async def get_predictions(
            agent_id: str = Depends(get_agent_id),
            state: GatewayState = Depends(get_gateway_state),
        ) -> PredictionsListResponse:
            """Get all predictions for the agent (prediction mode only)."""
            if not state.adapter.is_registered(agent_id):
                raise HTTPException(status_code=403, detail="Agent not registered")
            preds = await state.adapter.get_predictions(agent_id)
            return PredictionsListResponse(predictions=preds)

        @app.get("/event/info", response_model=EventInfoResponse)
        async def get_event_info(
            agent_id: str = Depends(get_agent_id),
            state: GatewayState = Depends(get_gateway_state),
        ) -> EventInfoResponse:
            """Get current event info (prediction mode only)."""
            if not state.adapter.is_registered(agent_id):
                raise HTTPException(status_code=403, detail="Agent not registered")
            info = await state.adapter.get_event_info()
            if info is None:
                raise HTTPException(status_code=404, detail="No active event")
            return info

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
            info: dict[str, Any] = {
                "agent_id": agent_id,
                "registered_at": agent_state.registered_at.isoformat(),
                "last_activity_at": agent_state.last_activity_at.isoformat()
                if agent_state.last_activity_at
                else None,
            }
            if state.adapter.contest_kind == "classic_betting":
                accounts = getattr(state.broker, "_accounts", {})
                if agent_id in accounts:
                    info["balance"] = str(accounts[agent_id].balance)
            agents.append(info)
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
        """Get leaderboard showing all agents' statistics.

        In classic betting mode returns balances and ROI; in prediction mode
        returns accuracy and scores.
        """
        leaderboard: list[dict[str, Any]] = []

        if state.broker.get_contest_kind() == "window_pool_prediction":
            # Use the adapter's isinstance-guarded narrowing property so a
            # misconfigured trial raises a clear RuntimeError instead of a
            # cryptic AttributeError that a bare cast() would let through.
            pred_broker = state.adapter._pred_broker
            pred_stats = pred_broker.get_prediction_statistics()

            all_agent_ids = set(pred_stats.keys())
            for agent_id in pred_broker.agents:
                all_agent_ids.add(agent_id)
            for agent_id in state.adapter._agents:
                all_agent_ids.add(agent_id)

            for agent_id in all_agent_ids:
                is_external = agent_id in state.adapter._agents
                pstats = pred_stats.get(agent_id)
                leaderboard.append(
                    {
                        "agent_id": agent_id,
                        "is_external": is_external,
                        "total_predictions": pstats.total_predictions if pstats else 0,
                        "correct_predictions": pstats.correct_predictions
                        if pstats
                        else 0,
                        "accuracy": round(pstats.accuracy, 4) if pstats else 0.0,
                        "total_score": str(pstats.total_score) if pstats else "0",
                    }
                )
            leaderboard.sort(key=lambda x: float(x["total_score"]), reverse=True)
        else:
            betting_broker = state.adapter._betting_broker
            for agent_id, account in betting_broker._accounts.items():
                stats = await betting_broker.get_statistics(agent_id)
                is_external = account.is_external
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
            leaderboard.sort(key=lambda x: float(x["balance"]), reverse=True)

        return {
            "trial_id": state.trial_id,
            "mode": "prediction"
            if state.adapter.contest_kind == "window_pool_prediction"
            else "classic_betting",
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
        accepting = state.broker.is_accepting()
        kind = state.broker.get_contest_kind()

        return {
            "status": "ok",
            "trial_id": state.trial_id,
            # ``kind`` is the canonical contest identifier (matches
            # GET /rules); ``mode`` is the legacy short alias kept for
            # existing clients.
            "kind": kind,
            "mode": "prediction" if kind == "window_pool_prediction" else kind,
            "registered_agents": len(state.adapter._agents),
            "accepting": accepting,
        }

    return app


__all__ = [
    "GatewayState",
    "create_gateway_app",
    "get_agent_id",
    "get_gateway_state",
]
