"""Agent Gateway FastAPI server.

Provides HTTP API for external agents to participate in trials.
Follows patterns from dashboard_server/_server.py.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from dojozero.gateway._activity import (
    emit_approval_denied,
    emit_approval_granted,
    emit_approval_requested,
    emit_auth_deny,
    emit_bet_decision,
    emit_model_call,
    emit_session_end,
    emit_session_start,
    emit_tool_use,
    emit_transfer_value,
)
from dojozero.gateway._adapter import ExternalAgentAdapter
from dojozero.gateway._approvals import (
    ApprovalCoordinator,
    ApprovalError,
    build_bet_facts,
    build_bet_summary,
)
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
    PendingApprovalResponse,
    PendingBetStatus,
    CurrentOddsResponse,
    ErrorCodes,
    ErrorDetail,
    ErrorResponse,
    EventEnvelope,
    ModelCallReport,
    RecentEventsResponse,
    TrialEndedInfo,
    TrialMetadataResponse,
    TrialResultsResponse,
)
from dojozero.gateway._sse import SSEConnection, create_sse_response

if TYPE_CHECKING:
    from agent_id_service_sdk import Verifier

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
    agentid_verifier: "Verifier | None" = None
    # Wall-clock time (``time.time()``) when each agent registered, keyed by
    # agent_id. Populated on session.start emission and consumed on
    # session.end so we can report a real ``duration_ms``. Best-effort: a
    # missing entry means duration is reported as 0.
    session_start_times: dict[str, float] = field(default_factory=dict)
    # Approval coordinator for the spec §7.6.7 grant flow. ``None`` when
    # the gateway has no AgentID verifier configured (approval mode is
    # only meaningful with a verified-agent path); the /bets handler
    # rejects approval-mode registrations in that case.
    approval_coordinator: "ApprovalCoordinator | None" = None


def get_gateway_state(request: Request) -> GatewayState:
    """Dependency to get gateway state from app.state."""
    state = getattr(request.app.state, "gateway_state", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Gateway not initialized")
    return state


async def get_agent_id(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    """Extract the verified agent_id from the AgentID Bearer token.

    The gateway authenticates external agents via ``Authorization: Bearer
    <token>``, verified against the configured AgentID provider. The returned
    string is the token's ``sub`` claim (e.g. ``agentid:<domain>:<uuid>``).

    Args:
        request: FastAPI request (used to access gateway state).
        authorization: ``Authorization`` header value.

    Returns:
        Verified agent ID string.

    Raises:
        HTTPException: 401 if the header is missing, malformed, or fails
            verification; 503 if the gateway has no AgentID verifier
            configured (operator misconfiguration).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCodes.AUTH_REQUIRED,
                    message="Authentication required: 'Authorization: Bearer <token>'",
                )
            ).model_dump(by_alias=True),
        )

    state = getattr(request.app.state, "gateway_state", None)
    verifier = getattr(state, "agentid_verifier", None) if state else None
    if verifier is None:
        # The gateway requires an AgentID verifier; refusing to authenticate
        # silently would mask a deployment misconfiguration.
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCodes.AUTH_REQUIRED,
                    message="AgentID provider is not configured on this gateway",
                )
            ).model_dump(by_alias=True),
        )

    return await _verify_agentid_token(
        authorization[7:],
        verifier,
        route=request.url.path,
    )


async def _verify_agentid_token(
    token: str,
    verifier: "Verifier",
    *,
    route: str | None = None,
) -> str:
    """Verify an AgentID token and return the agent_id, mapping errors to HTTP 401.

    On verification failure, emits an ``auth.deny`` activity event so the
    activity service has a record of the rejected attempt (see
    ``gateway/_activity.py``).
    """
    # Imports are local so the gateway works without agent-id-service-sdk
    # installed (AgentID auth just stays disabled).
    from agent_id_service_sdk.errors import (  # noqa: PLC0415
        ProviderUntrustedError,
        SignatureInvalidError,
        TokenExpiredError,
        TokenInvalidError,
    )

    try:
        agent = await verifier.verify_token(
            token,
            request_context={"route": route} if route else None,
        )
    except TokenExpiredError as exc:
        await emit_auth_deny(
            verifier, token=token, route=route or "", error_class="token_expired"
        )
        raise HTTPException(
            status_code=401,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCodes.TOKEN_EXPIRED,
                    message="AgentID token has expired",
                )
            ).model_dump(by_alias=True),
        ) from exc
    except TokenInvalidError as exc:
        await emit_auth_deny(
            verifier, token=token, route=route or "", error_class="token_invalid"
        )
        raise HTTPException(
            status_code=401,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCodes.INVALID_TOKEN,
                    message=f"Invalid AgentID token: {exc}",
                )
            ).model_dump(by_alias=True),
        ) from exc
    except ProviderUntrustedError as exc:
        await emit_auth_deny(
            verifier, token=token, route=route or "", error_class="provider_untrusted"
        )
        raise HTTPException(
            status_code=401,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCodes.INVALID_TOKEN,
                    message=f"Invalid AgentID token: {exc}",
                )
            ).model_dump(by_alias=True),
        ) from exc
    except SignatureInvalidError as exc:
        await emit_auth_deny(
            verifier, token=token, route=route or "", error_class="signature_invalid"
        )
        raise HTTPException(
            status_code=401,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCodes.INVALID_TOKEN,
                    message=f"Invalid AgentID token: {exc}",
                )
            ).model_dump(by_alias=True),
        ) from exc

    return agent.agent_id


def _build_approval_coordinator(
    verifier: "Verifier | None",
) -> ApprovalCoordinator | None:
    """Wire an :class:`ApprovalCoordinator` from gateway env, or return None.

    Returns ``None`` (approval mode disabled) when:

    - No AgentID verifier is configured (approval mode requires
      verified-agent auth to be meaningful).
    - The hub's ``service_id`` (audience) isn't set.
    - We can't resolve a single IdP URL to use for approvals.

    For v1 we assume a single trusted IdP for the approval flow. When
    multi-IdP support lands, this should look up the IdP per agent at
    submit time using the agent's ``iss`` claim.
    """
    if verifier is None:
        return None

    import os  # noqa: PLC0415

    hub_audience = (
        os.environ.get("DOJOZERO_HUB_SERVICE_ID")
        or os.environ.get("DOJOZERO_AGENTID_AUDIENCE", "")
    ).strip()
    if not hub_audience:
        logger.info(
            "approval coordinator disabled: no DOJOZERO_HUB_SERVICE_ID "
            "or DOJOZERO_AGENTID_AUDIENCE set"
        )
        return None

    # Either an explicit env override, or derive from the single configured IdP.
    approval_endpoint = os.environ.get("DOJOZERO_AGENTID_APPROVAL_ENDPOINT", "").strip()
    idp_base = ""
    trusted_issuers: set[str] = set()

    provider_urls_raw = os.environ.get("DOJOZERO_AGENTID_PROVIDER_URLS", "").strip()
    if provider_urls_raw:
        try:
            import json  # noqa: PLC0415

            provider_urls: dict[str, str] = json.loads(provider_urls_raw)
        except ValueError:
            provider_urls = {}
        if len(provider_urls) == 1:
            idp_base = next(iter(provider_urls.values())).rstrip("/")
        trusted_issuers = {url.rstrip("/") for url in provider_urls.values()}

    if not approval_endpoint and idp_base:
        approval_endpoint = f"{idp_base}/agentid/approvals"
    if not approval_endpoint:
        logger.info(
            "approval coordinator disabled: cannot resolve IdP approval endpoint "
            "(set DOJOZERO_AGENTID_APPROVAL_ENDPOINT or configure "
            "DOJOZERO_AGENTID_PROVIDER_URLS with a single entry)"
        )
        return None

    if not idp_base:
        # Fall back to deriving the JWKS URL from the approval endpoint.
        from urllib.parse import urlparse  # noqa: PLC0415

        parsed = urlparse(approval_endpoint)
        idp_base = f"{parsed.scheme}://{parsed.netloc}"
        if not trusted_issuers:
            trusted_issuers = {idp_base}

    jwks_url = f"{idp_base}/.well-known/agentid-jwks"

    logger.info(
        "approval coordinator enabled: endpoint=%s aud=%s issuers=%s",
        approval_endpoint,
        hub_audience,
        sorted(trusted_issuers),
    )
    return ApprovalCoordinator(
        approval_endpoint=approval_endpoint,
        hub_audience=hub_audience,
        trusted_issuers=trusted_issuers,
        idp_jwks_url=jwks_url,
    )


def create_gateway_app(
    trial_id: str,
    data_hub: "DataHub",
    broker: "BrokerOperator",
    metadata: dict[str, Any] | None = None,
    authenticator: AgentAuthenticator | None = None,
    agentid_verifier: "Verifier | None" = None,
) -> FastAPI:
    """Create the Agent Gateway FastAPI application.

    Args:
        trial_id: ID of the trial this gateway serves
        data_hub: DataHub instance for event subscriptions
        broker: BrokerOperator for betting operations
        metadata: Trial metadata
        authenticator: Optional authenticator for API key validation
            (used by registration flows). If None, uses NoOpAuthenticator.
        agentid_verifier: ``Verifier`` from ``agent-id-service-sdk``. The
            gateway authenticates all per-trial requests via Bearer tokens
            verified by this object — pass ``None`` only in tests. Build via
            :func:`dojozero.gateway._agentid.agentid_verifier_from_env`.

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

        # Approval coordinator wires up only when AgentID verification is
        # configured — approval mode is meaningful only with verified-agent
        # auth. _build_approval_coordinator returns None if any required
        # config (approval_endpoint, hub_audience, JWKS URL) is missing.
        approval_coordinator = _build_approval_coordinator(agentid_verifier)

        state = GatewayState(
            trial_id=trial_id,
            data_hub=data_hub,
            broker=broker,
            adapter=adapter,
            authenticator=auth,
            metadata=metadata or {},
            agentid_verifier=agentid_verifier,
            approval_coordinator=approval_coordinator,
        )

        app.state.gateway_state = state
        auth_status = "enabled" if auth.is_enabled() else "disabled"
        aip_status = "enabled" if agentid_verifier is not None else "disabled"
        approval_status = "enabled" if approval_coordinator is not None else "disabled"
        logger.info(
            "Gateway started for trial %s (auth: %s, aip: %s, approval: %s)",
            trial_id,
            auth_status,
            aip_status,
            approval_status,
        )

        yield

        if approval_coordinator is not None:
            await approval_coordinator.aclose()

        # Emit session.end for any agent that didn't unregister cleanly
        # before the gateway shut down (process killed, network gone,
        # trial ended without explicit DELETE /agents). Agents that DID
        # unregister already had session.end emitted and were popped from
        # session_start_times in the unregister handler.
        if state.session_start_times:
            now = time.time()
            last_seq = state.data_hub.subscription_manager.global_sequence
            for stranded_agent_id, started_at in list(
                state.session_start_times.items()
            ):
                duration_ms = int((now - started_at) * 1000)
                try:
                    final_balance = str(
                        getattr(adapter.get_balance(stranded_agent_id), "balance", "")
                    )
                except Exception:  # noqa: BLE001
                    final_balance = None
                await emit_session_end(
                    state.agentid_verifier,
                    agent_id=stranded_agent_id,
                    trial_id=state.trial_id,
                    duration_ms=duration_ms,
                    final_balance=final_balance,
                    last_observed_sequence=last_seq,
                )
            state.session_start_times.clear()

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
        authorization: str | None = Header(default=None),
    ) -> AgentRegistrationResponse:
        """Register an external agent for this trial.

        Two auth paths:

        - **AgentID Bearer JWT (preferred)**. When ``Authorization: Bearer
          <jwt>`` is present and the gateway has an ``agentid_verifier``
          configured, identity is taken from the cryptographically
          verified ``sub`` claim. The legacy ``apiKey`` body field is
          ignored.
        - **Legacy apiKey** (back-compat for pre-AgentID clients). When
          no Bearer is present, ``apiKey`` is validated against the
          configured authenticator (agent_keys.yaml + GitHub PAT).
        """
        # Convert initial_balance to string if it's a float
        initial_balance: str | None = None
        if request.initial_balance is not None:
            initial_balance = str(request.initial_balance)

        bearer_agent_id: str | None = None
        if (
            authorization
            and authorization.startswith("Bearer ")
            and state.agentid_verifier is not None
        ):
            token = authorization[len("Bearer ") :]
            try:
                bearer_agent_id = await _verify_agentid_token(
                    token, state.agentid_verifier
                )
            except HTTPException:
                # 401 etc — propagate; refuse a valid-looking-but-bad token.
                raise

        if bearer_agent_id is not None:
            # Bearer is the source of truth. Pull richer metadata from
            # agent_keys.yaml *if* the agent is pre-registered there;
            # otherwise default everything from the verified agent_id.
            yaml_identity = None
            try:
                yaml_identity = await state.authenticator.validate(request.api_key)
            except Exception:  # noqa: BLE001
                yaml_identity = None
            if yaml_identity is not None and yaml_identity.agent_id != bearer_agent_id:
                raise HTTPException(
                    status_code=403,
                    detail=ErrorResponse(
                        error=ErrorDetail(
                            code=ErrorCodes.INVALID_TOKEN,
                            message=(
                                f"apiKey identity {yaml_identity.agent_id!r} "
                                f"does not match Bearer identity "
                                f"{bearer_agent_id!r}"
                            ),
                        )
                    ).model_dump(by_alias=True),
                )
            agent_id = bearer_agent_id
            display_name = yaml_identity.display_name if yaml_identity else agent_id
            persona = yaml_identity.persona if yaml_identity else None
            model = yaml_identity.model if yaml_identity else None
            model_display_name = (
                yaml_identity.model_display_name if yaml_identity else None
            )
            cdn_url = yaml_identity.cdn_url if yaml_identity else None
            logger.info(
                "Agent authenticated via Bearer: agent_id=%s persona=%s",
                agent_id,
                persona or "(none)",
            )
        else:
            # Legacy apiKey path — used by pre-AgentID clients and the
            # existing test suite. The authenticator validates the body
            # against agent_keys.yaml or a GitHub PAT.
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
            agent_id = identity.agent_id
            display_name = identity.display_name
            persona = identity.persona
            model = identity.model
            model_display_name = identity.model_display_name
            cdn_url = identity.cdn_url
            logger.info(
                "Agent authenticated: api_key=***%s, agent_id=%s, persona=%s",
                request.api_key[-4:] if len(request.api_key) > 4 else "****",
                agent_id,
                persona or "(none)",
            )

        try:
            response = await state.adapter.register_agent(
                agent_id=agent_id,
                initial_balance=initial_balance,
                display_name=display_name,
                persona=persona,
                model=model,
                model_display_name=model_display_name,
                cdn_url=cdn_url,
                authenticated=True,
                request_approval=request.request_approval,
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

        # Activity event: agent joined the trial. Best-effort — emission
        # never raises and never blocks registration.
        state.session_start_times[agent_id] = time.time()
        await emit_session_start(
            state.agentid_verifier,
            agent_id=agent_id,
            agent_name=display_name or "",
            trial_id=state.trial_id,
            persona=persona,
            model=model,
            sport_type=state.metadata.get("sport_type"),
        )
        return response

    @app.post("/agents/reconnect", response_model=AgentRegistrationResponse)
    async def reconnect_agent(
        request: AgentReconnectRequest,
        state: GatewayState = Depends(get_gateway_state),
    ) -> AgentRegistrationResponse:
        """Reconnect an existing agent using API key and session key.

        Requires both the API key (for identity verification) and the session key
        (returned during original registration) to prove ownership of the session.
        """
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
        # Capture pre-unregister info for the session.end activity event
        # before adapter state is torn down.
        last_observed_sequence = state.data_hub.subscription_manager.global_sequence
        final_balance: str | None = None
        try:
            balance_obj = state.adapter.get_balance(agent_id)
            final_balance = str(getattr(balance_obj, "balance", ""))
        except Exception:  # noqa: BLE001 — diagnostic only, never block unregister
            final_balance = None

        try:
            if await state.adapter.unregister_agent(agent_id, request.session_key):
                started_at = state.session_start_times.pop(agent_id, None)
                duration_ms = (
                    int((time.time() - started_at) * 1000)
                    if started_at is not None
                    else 0
                )

                await emit_session_end(
                    state.agentid_verifier,
                    agent_id=agent_id,
                    trial_id=state.trial_id,
                    duration_ms=duration_ms,
                    final_balance=final_balance,
                    last_observed_sequence=last_observed_sequence,
                )
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

    async def _execute_bet_placement(
        state: GatewayState,
        agent_id: str,
        request: BetRequest,
    ) -> BetResponse:
        """Place a bet via the broker and emit the activity it implies.

        Shared between the direct ``/bets`` path (no approval needed)
        and the post-approval ``/bets/pending/{approval_id}`` path
        (after consuming a grant). Treats the call as a
        ``dojozero.place_bet`` tool invocation and emits accordingly:
        ``tool.use`` either way, plus ``transfer.value`` and
        ``dojozero.bet_decision`` on success.

        Raises an ``HTTPException`` translated from the broker's
        ``ValueError`` codes — same mapping the inline handler used
        before this was lifted out.
        """
        bet_args = request.model_dump(by_alias=False, exclude_none=True)
        tool_started = time.monotonic()

        try:
            response = await state.adapter.place_bet(agent_id, request)
        except ValueError as e:
            duration_ms = int((time.monotonic() - tool_started) * 1000)
            await emit_tool_use(
                state.agentid_verifier,
                agent_id=agent_id,
                trial_id=state.trial_id,
                tool_name="dojozero.place_bet",
                args=bet_args,
                duration_ms=duration_ms,
                success=False,
            )

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

        duration_ms = int((time.monotonic() - tool_started) * 1000)
        await emit_tool_use(
            state.agentid_verifier,
            agent_id=agent_id,
            trial_id=state.trial_id,
            tool_name="dojozero.place_bet",
            args=bet_args,
            duration_ms=duration_ms,
            success=True,
            linked_transfer_id=response.bet_id,
        )
        await emit_transfer_value(
            state.agentid_verifier,
            agent_id=agent_id,
            trial_id=state.trial_id,
            amount=response.amount,
            bet_id=response.bet_id,
        )
        sport = state.metadata.get("sport_type") if state.metadata else None
        game_id = (
            getattr(state.broker._event, "event_id", None)
            if state.broker._event is not None
            else None
        )
        await emit_bet_decision(
            state.agentid_verifier,
            agent_id=agent_id,
            trial_id=state.trial_id,
            bet_id=response.bet_id,
            market=request.market,
            selection=request.selection,
            amount=response.amount,
            model=request.model,
            confidence=request.confidence,
            rationale=request.rationale,
            sport=sport,
            game_id=game_id,
        )
        return response

    @app.post("/bets")
    async def place_bet(
        request: BetRequest,
        fastapi_request: Request,
        agent_id: str = Depends(get_agent_id),
        state: GatewayState = Depends(get_gateway_state),
    ):
        """Place a bet — directly, or via the approval gate if the agent
        is registered with ``request_approval=True``.

        Direct path returns ``BetResponse`` (the historical shape).

        Approval path returns ``PendingApprovalResponse``. The agent is
        expected to poll ``GET /bets/pending/{approval_id}`` until the
        principal decides; that endpoint returns a ``BetResponse`` on
        approve-and-placed or a ``PendingBetStatus`` otherwise.
        """
        if not state.adapter.is_registered(agent_id):
            raise HTTPException(status_code=403, detail="Agent not registered")

        if state.adapter.is_approval_required(agent_id):
            coordinator = state.approval_coordinator
            if coordinator is None:
                # Agent registered in approval mode but the gateway can't
                # reach an IdP — operator misconfig. Fail closed: refuse
                # to place a bet that should have been gated.
                raise HTTPException(
                    status_code=503,
                    detail=ErrorResponse(
                        error=ErrorDetail(
                            code=ErrorCodes.AUTH_REQUIRED,
                            message=(
                                "approval mode requires an IdP-backed approval "
                                "coordinator; gateway has none configured"
                            ),
                        )
                    ).model_dump(by_alias=True),
                )
            try:
                pending = await coordinator.submit(
                    agent_id=agent_id,
                    bet_request=request,
                    summary=build_bet_summary(request),
                    facts=build_bet_facts(request, state.trial_id),
                    hub_id=coordinator._hub_audience,
                )
            except ApprovalError as exc:
                raise HTTPException(
                    status_code=exc.status_hint,
                    detail=ErrorResponse(
                        error=ErrorDetail(
                            code=ErrorCodes.AUTH_REQUIRED,
                            message=str(exc),
                        )
                    ).model_dump(by_alias=True),
                ) from exc

            await emit_approval_requested(
                state.agentid_verifier,
                agent_id=agent_id,
                trial_id=state.trial_id,
                approval_id=pending.approval_id,
                resource="dojozero.place_bet",
            )
            base = str(fastapi_request.url).rstrip("/")
            # `fastapi_request.url` is the full POST /bets URL — strip the
            # /bets segment to get the per-trial base, then add the pending
            # path. Keeps poll_url valid even if the gateway is mounted under
            # a per-trial prefix (e.g. /api/trials/<id>/bets).
            if base.endswith("/bets"):
                base = base[: -len("/bets")]
            return PendingApprovalResponse(
                approval_id=pending.approval_id,
                expires_at=pending.expires_at,
                poll_url=f"{base}/bets/pending/{pending.approval_id}",
                summary=build_bet_summary(request),
            )

        return await _execute_bet_placement(state, agent_id, request)

    @app.get("/bets/pending/{approval_id}")
    async def poll_pending_bet(
        approval_id: str,
        agent_id: str = Depends(get_agent_id),
        state: GatewayState = Depends(get_gateway_state),
    ):
        """Resolve a pending bet by polling the IdP approval status.

        On approved: verify the IdP's decision JWT, mint and consume an
        internal grant, place the bet, return ``BetResponse``. The
        runner's tool layer surfaces this as the same shape it would
        have gotten from a direct ``POST /bets``.

        On still-pending / denied / expired: return ``PendingBetStatus``
        so the agent can react in conversation.
        """
        if not state.adapter.is_registered(agent_id):
            raise HTTPException(status_code=403, detail="Agent not registered")
        coordinator = state.approval_coordinator
        if coordinator is None:
            raise HTTPException(
                status_code=503,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCodes.AUTH_REQUIRED,
                        message="approval coordinator not configured",
                    )
                ).model_dump(by_alias=True),
            )

        try:
            poll = await coordinator.poll(approval_id, agent_id=agent_id)
        except ApprovalError as exc:
            raise HTTPException(
                status_code=exc.status_hint,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCodes.AUTH_REQUIRED,
                        message=str(exc),
                    )
                ).model_dump(by_alias=True),
            ) from exc

        if poll.status == "approved":
            assert poll.grant_id is not None and poll.bet_request is not None
            try:
                bet_request = await coordinator.consume_grant(
                    poll.grant_id, agent_id=agent_id
                )
            except ApprovalError as exc:
                raise HTTPException(
                    status_code=exc.status_hint,
                    detail=ErrorResponse(
                        error=ErrorDetail(
                            code=ErrorCodes.AUTH_REQUIRED,
                            message=str(exc),
                        )
                    ).model_dump(by_alias=True),
                ) from exc

            response = await _execute_bet_placement(state, agent_id, bet_request)
            await emit_approval_granted(
                state.agentid_verifier,
                agent_id=agent_id,
                trial_id=state.trial_id,
                approval_id=approval_id,
                decided_by=poll.decided_by,
                resource="dojozero.place_bet",
            )
            return response

        if poll.status == "denied":
            await emit_approval_denied(
                state.agentid_verifier,
                agent_id=agent_id,
                trial_id=state.trial_id,
                approval_id=approval_id,
                decided_by=poll.decided_by,
                reason=poll.denial_note,
                resource="dojozero.place_bet",
            )

        # poll.status is "pending" | "denied" | "expired" here — the
        # "approved" branch returned above. PollResult uses ``str`` for
        # forward-compat with future status values; PendingBetStatus
        # locks to the three we surface to clients.
        if poll.status not in ("pending", "denied", "expired"):
            raise HTTPException(
                status_code=502,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCodes.AUTH_REQUIRED,
                        message=f"unexpected approval status: {poll.status!r}",
                    )
                ).model_dump(by_alias=True),
            )
        return PendingBetStatus(
            status=poll.status,  # type: ignore[arg-type]
            approval_id=approval_id,
            expires_at=poll.expires_at,
            denial_note=poll.denial_note,
            decided_by=poll.decided_by,
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

            # Check if this is an external agent (Account is single source of truth)
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

    # =========================================================================
    # Activity reports from runners (forwarded to aip-activity)
    # =========================================================================

    @app.post("/activity/model-call", status_code=202)
    async def report_model_call(
        report: ModelCallReport,
        agent_id: str = Depends(get_agent_id),
        state: GatewayState = Depends(get_gateway_state),
    ) -> dict[str, Any]:
        """Runner-side ``model.call`` forwarder.

        Runners can't talk to aip-activity directly (the hub is the
        single emission source). They post LLM usage here after each
        chat-model call; the gateway forwards as a Tier-1 ``model.call``
        event scoped to the runner's authenticated agent_id.
        """
        if not state.adapter.is_registered(agent_id):
            raise HTTPException(status_code=403, detail="Agent not registered")
        await emit_model_call(
            state.agentid_verifier,
            agent_id=agent_id,
            trial_id=state.trial_id,
            model=report.model,
            tokens_in=report.tokens_in,
            tokens_out=report.tokens_out,
            latency_ms=report.latency_ms,
            cost_usd=report.cost_usd,
            purpose=report.purpose,
            outcome=report.outcome,
            linked_tool_invocation_id=report.linked_tool_invocation_id,
        )
        return {"accepted": True}

    # AgentID hub-discovery routes used to live on this per-trial app.
    # They moved to the dashboard server because hub identity is
    # process-wide, not trial-scoped — see
    # ``dashboard_server/_server.py`` and ``gateway/_hub_routes.py``.

    return app


__all__ = [
    "GatewayState",
    "create_gateway_app",
    "get_agent_id",
    "get_gateway_state",
]
