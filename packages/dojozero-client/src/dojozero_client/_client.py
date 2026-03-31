"""DojoZero client for external agents.

Provides high-level API for connecting to trials and placing bets.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from dojozero_client._exceptions import (
    ConnectionError,
    RegistrationError,
    TrialEndedError,
)
from dojozero_client._transport import GatewayTransport

logger = logging.getLogger(__name__)


@dataclass
class BetResult:
    """Result of a bet placement."""

    bet_id: str
    agent_id: str
    market: str
    selection: str
    amount: float
    probability: float
    status: str
    placed_at: datetime
    reference_sequence: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BetResult":
        """Create from API response."""
        # Server sends createdAt, handle both for compatibility
        placed_at_str = data.get("placedAt") or data.get("createdAt")
        placed_at = (
            datetime.fromisoformat(placed_at_str.replace("Z", "+00:00"))
            if placed_at_str
            else datetime.now(timezone.utc)
        )
        return cls(
            bet_id=data["betId"],
            agent_id=data["agentId"],
            market=data["market"],
            selection=data["selection"],
            amount=float(data["amount"])
            if isinstance(data["amount"], str)
            else data["amount"],
            probability=float(data["probability"])
            if isinstance(data["probability"], str)
            else data["probability"],
            status=data["status"],
            placed_at=placed_at,
            reference_sequence=data.get("referenceSequence", 0),
        )


@dataclass
class Holding:
    """A single holding position."""

    event_id: str
    selection: str
    bet_type: str
    shares: float
    avg_probability: float
    spread_value: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Holding":
        """Create from API response."""
        return cls(
            event_id=data.get("eventId", ""),
            selection=data.get("selection", ""),
            bet_type=data.get("betType", ""),
            shares=float(data.get("shares", 0)),
            avg_probability=float(data.get("avgProbability", 0)),
            spread_value=float(data["spreadValue"])
            if data.get("spreadValue")
            else None,
        )


@dataclass
class Balance:
    """Agent balance information."""

    agent_id: str
    balance: float
    holdings: list[Holding]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Balance":
        """Create from API response."""
        raw_holdings = data.get("holdings", [])
        holdings = [Holding.from_dict(h) for h in raw_holdings] if raw_holdings else []
        return cls(
            agent_id=data["agentId"],
            balance=float(data["balance"]),
            holdings=holdings,
        )


@dataclass
class Odds:
    """Current betting odds."""

    event_id: str
    home_probability: float
    away_probability: float
    betting_open: bool
    sequence: int
    home_team: str | None = None
    away_team: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Odds":
        """Create from API response."""
        return cls(
            event_id=data["eventId"],
            home_probability=data["homeProbability"],
            away_probability=data["awayProbability"],
            betting_open=data["bettingOpen"],
            sequence=data["sequence"],
            home_team=data.get("homeTeam"),
            away_team=data.get("awayTeam"),
        )


@dataclass
class TrialMetadata:
    """Trial information."""

    trial_id: str
    phase: str
    sport_type: str
    game_id: str
    home_team: str
    away_team: str
    game_time: datetime | None
    metadata: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrialMetadata":
        """Create from API response."""
        game_time = None
        if data.get("gameTime"):
            game_time = datetime.fromisoformat(data["gameTime"].replace("Z", "+00:00"))
        return cls(
            trial_id=data["trialId"],
            phase=data["phase"],
            sport_type=data.get("sportType", ""),
            game_id=data.get("gameId", ""),
            home_team=data.get("homeTeam", ""),
            away_team=data.get("awayTeam", ""),
            game_time=game_time,
            metadata=data.get("metadata", {}),
        )


@dataclass
class EventEnvelope:
    """Event envelope from SSE or polling."""

    trial_id: str
    sequence: int
    timestamp: datetime
    payload: dict[str, Any]
    event_type: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventEnvelope":
        """Create from API response."""
        return cls(
            trial_id=data["trialId"],
            sequence=data["sequence"],
            timestamp=datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00")),
            payload=data["payload"],
            event_type=data.get("type"),
        )


@dataclass
class AgentResult:
    """Final results for a single agent."""

    agent_id: str
    final_balance: float
    net_profit: float
    total_bets: int
    win_rate: float
    roi: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentResult":
        """Create from API response."""
        return cls(
            agent_id=data["agentId"],
            final_balance=float(data["finalBalance"]),
            net_profit=float(data["netProfit"]),
            total_bets=data["totalBets"],
            win_rate=data["winRate"],
            roi=data["roi"],
        )


@dataclass
class TrialEndedEvent:
    """Trial ended notification received via SSE.

    This event signals that the trial has ended and provides final results.
    After receiving this event, the SSE stream will close.
    """

    trial_id: str
    reason: str  # "completed", "cancelled", "failed"
    timestamp: datetime
    final_results: list[AgentResult]
    message: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrialEndedEvent":
        """Create from SSE event data."""
        return cls(
            trial_id=data["trialId"],
            reason=data["reason"],
            timestamp=datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00")),
            final_results=[
                AgentResult.from_dict(r) for r in data.get("finalResults", [])
            ],
            message=data.get("message", ""),
        )


@dataclass
class TrialResults:
    """Trial results from results endpoint."""

    trial_id: str
    status: str  # "running", "completed", "cancelled", "failed"
    results: list[AgentResult]
    ended_at: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrialResults":
        """Create from API response."""
        ended_at = None
        if data.get("endedAt"):
            ended_at = datetime.fromisoformat(data["endedAt"].replace("Z", "+00:00"))
        return cls(
            trial_id=data["trialId"],
            status=data["status"],
            results=[AgentResult.from_dict(r) for r in data.get("results", [])],
            ended_at=ended_at,
        )


class TrialConnection:
    """Connection to a specific trial.

    Provides methods to:
    - Stream events via SSE
    - Poll for recent events
    - Place bets
    - Query balance and odds

    Usage:
        async with client.connect_trial("http://localhost:8080", "my-agent") as trial:
            async for event in trial.events():
                if should_bet(event):
                    await trial.place_bet(
                        market="moneyline",
                        selection="home",
                        amount=100,
                        reference_sequence=event.sequence,
                    )
    """

    def __init__(
        self,
        transport: GatewayTransport,
        agent_id: str,
        trial_id: str,
        session_key: str = "",
    ):
        """Initialize trial connection.

        Args:
            transport: Gateway transport layer
            agent_id: Agent ID
            trial_id: Trial ID from registration
            session_key: Session key for secure reconnection/unregistration
        """
        self._transport = transport
        self._agent_id = agent_id
        self._trial_id = trial_id
        self._session_key = session_key
        self._last_sequence: int = 0
        self._trial_ended: TrialEndedEvent | None = None

    @property
    def agent_id(self) -> str:
        """Get agent ID."""
        return self._agent_id

    @property
    def trial_id(self) -> str:
        """Get trial ID."""
        return self._trial_id

    @property
    def session_key(self) -> str:
        """Get session key for secure reconnection/unregistration."""
        return self._session_key

    @property
    def last_sequence(self) -> int:
        """Get last seen sequence number."""
        return self._last_sequence

    @property
    def trial_ended(self) -> TrialEndedEvent | None:
        """Get trial ended event if trial has ended via SSE."""
        return self._trial_ended

    def set_resume_sequence(self, sequence: int) -> None:
        """Set sequence to resume from on reconnection.

        Call this before events() to replay missed events from the server.
        The server will replay events since this sequence (up to 100 events).

        Args:
            sequence: Last event sequence seen before disconnect
        """
        self._last_sequence = sequence
        self._transport.set_last_event_id(sequence)

    async def get_trial_metadata(self) -> TrialMetadata:
        """Get trial metadata."""
        response = await self._transport.request("GET", "/trial")
        return TrialMetadata.from_dict(response)

    async def events(
        self,
        event_types: list[str] | None = None,
        raise_on_trial_end: bool = True,
    ) -> AsyncIterator[EventEnvelope]:
        """Stream events via SSE.

        Args:
            event_types: Optional list of event types to filter
            raise_on_trial_end: If True (default), raises TrialEndedError when
                trial_ended event is received. If False, logs and returns normally.

        Yields:
            EventEnvelope objects as they arrive

        Raises:
            StreamDisconnectedError: If stream disconnects unexpectedly
            TrialEndedError: If trial ends (when raise_on_trial_end=True)
        """
        async for sse_event in self._transport.stream_events():
            if sse_event.event == "event":
                try:
                    data = sse_event.json()
                    envelope = EventEnvelope.from_dict(data)

                    # Update last sequence
                    if envelope.sequence > self._last_sequence:
                        self._last_sequence = envelope.sequence

                    # Filter by event type if specified
                    if event_types:
                        # Check both snake_case and camelCase keys
                        event_type = envelope.payload.get(
                            "event_type", envelope.payload.get("eventType", "")
                        )
                        if not any(
                            self._matches_filter(event_type, f) for f in event_types
                        ):
                            continue

                    yield envelope
                except Exception as e:
                    logger.warning("Failed to parse event: %s", e)
                    continue

            elif sse_event.event == "trial_ended":
                # Trial has ended - parse the event
                try:
                    data = sse_event.json()
                    trial_ended = TrialEndedEvent.from_dict(data)
                    self._trial_ended = trial_ended

                    logger.info(
                        "Trial %s ended: reason=%s, agents=%d",
                        trial_ended.trial_id,
                        trial_ended.reason,
                        len(trial_ended.final_results),
                    )

                    if raise_on_trial_end:
                        raise TrialEndedError(
                            f"Trial {trial_ended.trial_id} has {trial_ended.reason}",
                            reason=trial_ended.reason,
                            final_results=trial_ended.final_results,
                        )
                    else:
                        # Return normally - stream will end
                        return

                except TrialEndedError:
                    raise
                except Exception as e:
                    logger.warning("Failed to parse trial_ended event: %s", e)
                    return

            elif sse_event.event == "heartbeat":
                # Update sequence from heartbeat
                try:
                    data = sse_event.json()
                    seq = data.get("sequence", 0)
                    if seq > self._last_sequence:
                        self._last_sequence = seq
                except Exception:
                    pass

            elif sse_event.event == "error":
                logger.error("SSE error: %s", sse_event.data)

    def _matches_filter(self, event_type: str, pattern: str) -> bool:
        """Check if event type matches filter pattern.

        Supports wildcards like 'event.nba_*'.
        """
        if "*" not in pattern:
            return event_type == pattern

        # Simple wildcard matching
        prefix = pattern.rstrip("*")
        return event_type.startswith(prefix)

    async def poll_events(
        self,
        since: int | None = None,
        limit: int = 50,
        event_types: list[str] | None = None,
    ) -> list[EventEnvelope]:
        """Poll for recent events.

        Args:
            since: Get events after this sequence number
            limit: Maximum events to return
            event_types: Optional event type filter

        Returns:
            List of EventEnvelope objects
        """
        params: dict[str, Any] = {"limit": limit}
        if since is not None:
            params["since"] = since
        if event_types:
            params["event_types"] = ",".join(event_types)

        response = await self._transport.request(
            "GET",
            "/events/recent",
            params=params,
        )

        events = [EventEnvelope.from_dict(e) for e in response.get("events", [])]

        # Update last sequence
        current_seq = response.get("currentSequence", 0)
        if current_seq > self._last_sequence:
            self._last_sequence = current_seq

        return events

    async def get_current_odds(self) -> Odds:
        """Get current betting odds.

        Returns:
            Current odds information
        """
        response = await self._transport.request("GET", "/odds/current")
        return Odds.from_dict(response)

    async def place_bet(
        self,
        market: str,
        selection: str,
        amount: float,
        reference_sequence: int | None = None,
        idempotency_key: str | None = None,
        spread_value: float | None = None,
        total_value: float | None = None,
    ) -> BetResult:
        """Place a bet.

        Args:
            market: Market to bet on (e.g., "moneyline")
            selection: Selection (e.g., "home", "away")
            amount: Bet amount
            reference_sequence: Sequence number for staleness check
            idempotency_key: Optional key for deduplication
            spread_value: Spread value for spread bets (e.g., -3.5)
            total_value: Total value for total bets (e.g., 215.5)

        Returns:
            BetResult with placement details

        Raises:
            StaleReferenceError: If reference_sequence is stale
            InsufficientBalanceError: If balance too low
            BettingClosedError: If betting is closed
            BetRejectedError: For other rejection reasons
        """
        body: dict[str, Any] = {
            "market": market,
            "selection": selection,
            "amount": str(amount),  # Server expects string for decimal precision
        }

        if reference_sequence is not None:
            body["referenceSequence"] = reference_sequence
        if idempotency_key:
            body["idempotencyKey"] = idempotency_key
        if spread_value is not None:
            body["spreadValue"] = spread_value
        if total_value is not None:
            body["totalValue"] = total_value

        response = await self._transport.request(
            "POST",
            "/bets",
            json=body,
        )

        return BetResult.from_dict(response)

    async def get_bets(self) -> list[BetResult]:
        """Get all bets for this agent.

        Returns:
            List of BetResult objects
        """
        response = await self._transport.request("GET", "/bets")
        return [BetResult.from_dict(b) for b in response.get("bets", [])]

    async def get_balance(self) -> Balance:
        """Get current balance and holdings.

        Returns:
            Balance information
        """
        response = await self._transport.request("GET", "/balance")
        return Balance.from_dict(response)

    async def get_results(self) -> TrialResults:
        """Get current or final trial results.

        Can be called during a running trial to get current standings,
        or after trial ends to get final results.

        This is useful if you missed the trial_ended SSE event or want
        to verify the final results.

        Returns:
            TrialResults with status and all agent results
        """
        response = await self._transport.request("GET", "/trial/results")
        return TrialResults.from_dict(response)


@dataclass
class GatewayInfo:
    """Information about an available trial gateway."""

    trial_id: str
    endpoint: str
    url: str | None = None  # Full URL for connection (set by discover_trials)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GatewayInfo":
        """Create from API response."""
        return cls(
            trial_id=data["trial_id"],
            endpoint=data["endpoint"],
            url=data.get("url"),
        )


class DojoClient:
    """DojoZero client for external agents.

    Standalone mode (dojo0 run):
        ```
        async with client.connect_trial(
            gateway_url="http://localhost:8080",
            api_key="sk-agent-xxx",  # From: dojo0 agents add --id my-agent
        ) as trial:
            async for event in trial.events():
                ...
        ```

    Dashboard mode (dojo0 serve):
        ```
        # Discover available trials (queries all configured dashboards)
        gateways = await client.discover_trials()

        # Connect using gateway info
        async with client.connect_trial(
            gateway_url=gateways[0].url,
            api_key="sk-agent-xxx",
        ) as trial:
            async for event in trial.events():
                ...
        ```

    Configuration (layered precedence):
        1. Constructor arguments
        2. Environment variables (DOJOZERO_DASHBOARD_URL, DOJOZERO_DASHBOARD_URLS)
        3. Config file (~/.dojozero/config.yaml)
        4. Defaults (http://localhost:8000)
    """

    def __init__(
        self,
        dashboard_url: str | None = None,
        dashboard_urls: list[str] | None = None,
        timeout: float = 30.0,
    ):
        """Initialize DojoZero client.

        Args:
            dashboard_url: Dashboard URL (single server mode)
            dashboard_urls: List of dashboard URLs (sharded mode)
            timeout: Default request timeout in seconds
        """
        from dojozero_client._config import load_config

        self._config = load_config(
            dashboard_url=dashboard_url,
            dashboard_urls=dashboard_urls,
            timeout=timeout,
        )
        self._timeout = self._config.timeout

    async def discover_trials(self) -> list[GatewayInfo]:
        """Discover all available trials across configured dashboards.

        Queries all dashboard URLs from config and aggregates results.
        Each GatewayInfo includes the full URL for connection.

        Returns:
            List of GatewayInfo with trial_id and full url

        Raises:
            ConnectionError: If all dashboards are unreachable
        """
        import asyncio

        urls = self._config.get_discovery_urls()
        tasks = [self.list_gateways(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_gateways: list[GatewayInfo] = []
        error_count = 0

        for url, result in zip(urls, results):
            if isinstance(result, BaseException):
                error_count += 1
                logger.warning("Failed to query %s: %s", url, result)
            else:
                # Add full URL to each gateway
                gateways: list[GatewayInfo] = result
                for gw in gateways:
                    gw_with_url = GatewayInfo(
                        trial_id=gw.trial_id,
                        endpoint=gw.endpoint,
                        url=f"{url.rstrip('/')}{gw.endpoint}",
                    )
                    all_gateways.append(gw_with_url)

        if not all_gateways and error_count > 0:
            raise ConnectionError(f"All {error_count} dashboards unreachable")

        return all_gateways

    async def list_gateways(self, dashboard_url: str) -> list[GatewayInfo]:
        """List available trial gateways from a dashboard server.

        Use this to discover which trials are available before connecting.

        Args:
            dashboard_url: Dashboard server URL (e.g., "http://localhost:8000")

        Returns:
            List of GatewayInfo with trial_id and endpoint for each

        Raises:
            ConnectionError: If dashboard is unreachable
        """
        import httpx

        url = f"{dashboard_url.rstrip('/')}/api/gateways"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    raise ConnectionError(
                        f"Failed to list gateways: {response.status_code}"
                    )
                data = response.json()
                return [GatewayInfo.from_dict(g) for g in data.get("gateways", [])]
        except httpx.ConnectError as e:
            raise ConnectionError(f"Cannot connect to dashboard: {e}") from e

    @asynccontextmanager
    async def connect_trial(
        self,
        gateway_url: str,
        api_key: str,
        initial_balance: float | None = None,
        session_key: str | None = None,
    ) -> AsyncIterator[TrialConnection]:
        """Connect to a trial.

        Args:
            gateway_url: Gateway URL (e.g., "http://localhost:8080")
            api_key: API key for authentication (from dojo0 agents add).
                     Agent identity (agent_id, display_name, persona, model)
                     all come from agent_keys.yaml based on this key.
            initial_balance: Starting balance (if registering)
            session_key: Session key from previous connection (for secure reconnection)

        Yields:
            TrialConnection for interacting with the trial.
            Access connection.session_key to get the session key for storage.

        Raises:
            ConnectionError: If connection fails
            RegistrationError: If registration fails
            AuthenticationError: If api_key is invalid
        """
        transport = GatewayTransport(
            base_url=gateway_url,
            timeout=self._timeout,
        )

        async with transport:
            trial_id = ""
            agent_id = ""
            new_session_key = ""

            # Try reconnect first if we have a session key
            if session_key:
                try:
                    reconnect_response = await transport.request(
                        "POST",
                        "/agents/reconnect",
                        json={
                            "apiKey": api_key,
                            "sessionKey": session_key,
                        },
                    )
                    trial_id = reconnect_response.get("trialId", "")
                    agent_id = reconnect_response.get("agentId", "")
                    new_session_key = reconnect_response.get("sessionKey", session_key)

                    # Set agent_id for subsequent requests
                    transport.set_agent_id(agent_id)

                    logger.info(
                        "Reconnected agent %s for trial %s using session key",
                        agent_id,
                        trial_id,
                    )
                except Exception as e:
                    # Reconnect failed - will try fresh registration below
                    logger.info(
                        "Session key reconnect failed, will try registration: %s", e
                    )
                    session_key = None  # Clear so we try registration

            # Try fresh registration if no session key or reconnect failed
            if not agent_id:
                try:
                    reg_response = await transport.request(
                        "POST",
                        "/agents",
                        json={
                            "apiKey": api_key,
                            "initialBalance": initial_balance,
                        },
                    )
                    trial_id = reg_response.get("trialId", "")
                    agent_id = reg_response.get("agentId", "")
                    new_session_key = reg_response.get("sessionKey", "")

                    # Set agent_id for subsequent requests
                    transport.set_agent_id(agent_id)

                    logger.info(
                        "Registered agent %s for trial %s",
                        agent_id,
                        trial_id,
                    )
                except RegistrationError as e:
                    # 409 Conflict - agent already registered but we don't have session key
                    # This is an error state - agent is connected elsewhere
                    error_msg = str(e)
                    if "already" in error_msg.lower():
                        raise ConnectionError(
                            f"Agent already connected elsewhere and no session key provided. "
                            f"Either stop the other connection or provide the session key. "
                            f"Original error: {error_msg}"
                        ) from e
                    raise

            connection = TrialConnection(
                transport=transport,
                agent_id=agent_id,
                trial_id=trial_id,
                session_key=new_session_key,
            )

            yield connection


__all__ = [
    "AgentResult",
    "Balance",
    "BetResult",
    "DojoClient",
    "EventEnvelope",
    "GatewayInfo",
    "Odds",
    "TrialConnection",
    "TrialEndedEvent",
    "TrialMetadata",
    "TrialResults",
]
