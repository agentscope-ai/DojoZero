"""DojoZero client for external agents.

Provides high-level API for connecting to trials and placing bets.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator

from dojozero_client._exceptions import (
    ConnectionError,
    NotRegisteredError,
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
        return cls(
            bet_id=data["betId"],
            agent_id=data["agentId"],
            market=data["market"],
            selection=data["selection"],
            amount=data["amount"],
            probability=data["probability"],
            status=data["status"],
            placed_at=datetime.fromisoformat(data["placedAt"].replace("Z", "+00:00")),
            reference_sequence=data.get("referenceSequence", 0),
        )


@dataclass
class Balance:
    """Agent balance information."""

    agent_id: str
    balance: float
    holdings: dict[str, float]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Balance":
        """Create from API response."""
        return cls(
            agent_id=data["agentId"],
            balance=data["balance"],
            holdings=data.get("holdings", {}),
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
    ):
        """Initialize trial connection.

        Args:
            transport: Gateway transport layer
            agent_id: Agent ID
            trial_id: Trial ID from registration
        """
        self._transport = transport
        self._agent_id = agent_id
        self._trial_id = trial_id
        self._last_sequence: int = 0

    @property
    def agent_id(self) -> str:
        """Get agent ID."""
        return self._agent_id

    @property
    def trial_id(self) -> str:
        """Get trial ID."""
        return self._trial_id

    @property
    def last_sequence(self) -> int:
        """Get last seen sequence number."""
        return self._last_sequence

    async def get_trial_metadata(self) -> TrialMetadata:
        """Get trial metadata."""
        response = await self._transport.request("GET", "/api/v1/trial")
        return TrialMetadata.from_dict(response)

    async def events(
        self,
        event_types: list[str] | None = None,
    ) -> AsyncIterator[EventEnvelope]:
        """Stream events via SSE.

        Args:
            event_types: Optional list of event types to filter

        Yields:
            EventEnvelope objects as they arrive

        Raises:
            StreamDisconnectedError: If stream disconnects
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
                        event_type = envelope.payload.get("eventType", "")
                        if not any(
                            self._matches_filter(event_type, f) for f in event_types
                        ):
                            continue

                    yield envelope
                except Exception as e:
                    logger.warning("Failed to parse event: %s", e)
                    continue

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
            "/api/v1/events/recent",
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
        response = await self._transport.request("GET", "/api/v1/odds/current")
        return Odds.from_dict(response)

    async def place_bet(
        self,
        market: str,
        selection: str,
        amount: float,
        reference_sequence: int | None = None,
        idempotency_key: str | None = None,
    ) -> BetResult:
        """Place a bet.

        Args:
            market: Market to bet on (e.g., "moneyline")
            selection: Selection (e.g., "home", "away")
            amount: Bet amount
            reference_sequence: Sequence number for staleness check
            idempotency_key: Optional key for deduplication

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
            "amount": amount,
        }

        if reference_sequence is not None:
            body["referenceSequence"] = reference_sequence
        if idempotency_key:
            body["idempotencyKey"] = idempotency_key

        response = await self._transport.request(
            "POST",
            "/api/v1/bets",
            json=body,
        )

        return BetResult.from_dict(response)

    async def get_bets(self) -> list[BetResult]:
        """Get all bets for this agent.

        Returns:
            List of BetResult objects
        """
        response = await self._transport.request("GET", "/api/v1/bets")
        return [BetResult.from_dict(b) for b in response.get("bets", [])]

    async def get_balance(self) -> Balance:
        """Get current balance and holdings.

        Returns:
            Balance information
        """
        response = await self._transport.request("GET", "/api/v1/balance")
        return Balance.from_dict(response)


class DojoClient:
    """DojoZero client for external agents.

    Usage:
        client = DojoClient()

        async with client.connect_trial(
            gateway_url="http://localhost:8080",
            agent_id="my-agent",
            persona="My betting agent",
        ) as trial:
            # Stream and react to events
            async for event in trial.events():
                odds = await trial.get_current_odds()
                if should_bet(event, odds):
                    await trial.place_bet(
                        market="moneyline",
                        selection="home",
                        amount=100,
                        reference_sequence=event.sequence,
                    )

            balance = await trial.get_balance()
            print(f"Final balance: {balance.balance}")
    """

    def __init__(
        self,
        timeout: float = 30.0,
    ):
        """Initialize DojoZero client.

        Args:
            timeout: Default request timeout in seconds
        """
        self._timeout = timeout

    @asynccontextmanager
    async def connect_trial(
        self,
        gateway_url: str,
        agent_id: str,
        persona: str | None = None,
        model: str | None = None,
        initial_balance: float | None = None,
        auto_register: bool = True,
    ) -> AsyncIterator[TrialConnection]:
        """Connect to a trial.

        Args:
            gateway_url: Gateway URL (e.g., "http://localhost:8080")
            agent_id: Unique agent identifier
            persona: Agent persona description
            model: Model identifier
            initial_balance: Starting balance (if registering)
            auto_register: Whether to auto-register if not registered

        Yields:
            TrialConnection for interacting with the trial

        Raises:
            ConnectionError: If connection fails
            RegistrationError: If registration fails
        """
        transport = GatewayTransport(
            base_url=gateway_url,
            agent_id=agent_id,
            timeout=self._timeout,
        )

        async with transport:
            trial_id = ""

            if auto_register:
                # Try to register (may already be registered)
                try:
                    reg_response = await transport.request(
                        "POST",
                        "/api/v1/register",
                        json={
                            "agentId": agent_id,
                            "persona": persona,
                            "model": model,
                            "initialBalance": initial_balance,
                        },
                    )
                    trial_id = reg_response.get("trialId", "")
                    logger.info(
                        "Registered agent %s for trial %s",
                        agent_id,
                        trial_id,
                    )
                except Exception as e:
                    # Check if already registered (409)
                    if "already" in str(e).lower():
                        logger.info(
                            "Agent %s already registered, continuing",
                            agent_id,
                        )
                    else:
                        raise

            # Get trial info if not from registration
            if not trial_id:
                try:
                    trial_response = await transport.request(
                        "GET",
                        "/api/v1/trial",
                    )
                    trial_id = trial_response.get("trialId", "unknown")
                except NotRegisteredError:
                    raise ConnectionError(
                        f"Agent {agent_id} not registered and auto_register=False"
                    )

            connection = TrialConnection(
                transport=transport,
                agent_id=agent_id,
                trial_id=trial_id,
            )

            yield connection


__all__ = [
    "Balance",
    "BetResult",
    "DojoClient",
    "EventEnvelope",
    "Odds",
    "TrialConnection",
    "TrialMetadata",
]
