"""Adapter bridging external agent HTTP API to internal Actor system.

The ExternalAgentAdapter provides the business logic layer between the
HTTP Gateway and the internal DataHub + BrokerOperator infrastructure.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from dojozero.betting._models import (
    BetRequestMoneyline,
    BetRequestSpread,
    BetRequestTotal,
    OrderType,
)
from dojozero.data._subscriptions import (
    Subscription,
    SubscriptionFilter,
    SubscriptionOptions,
)
from dojozero.gateway._models import (
    AgentRegistrationResponse,
    AgentResult,
    BalanceResponse,
    BetRequest,
    BetResponse,
    CurrentOddsResponse,
    HoldingResponse,
    SpreadLine,
    TotalLine,
    TrialEndedMessage,
)

if TYPE_CHECKING:
    from dojozero.betting._broker import BrokerOperator
    from dojozero.data import DataHub

logger = logging.getLogger(__name__)


@dataclass
class ExternalAgentState:
    """State for a registered external agent."""

    agent_id: str
    subscription: Subscription | None = None
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ExternalAgentAdapter:
    """Bridges external agent HTTP API to internal Actor protocol.

    Manages external agent registration, subscription, and betting through
    the existing DataHub and BrokerOperator infrastructure.

    This adapter handles:
    - Agent registration and account creation
    - Event subscriptions with filtering
    - Bet placement with staleness checks
    - Balance and holdings queries
    - Idempotency for bet deduplication
    """

    def __init__(
        self,
        data_hub: "DataHub",
        broker: "BrokerOperator",
        trial_id: str,
        max_sequence_staleness: int = 10,
    ):
        """Initialize adapter.

        Args:
            data_hub: DataHub instance for event subscriptions
            broker: BrokerOperator for betting operations
            trial_id: ID of the trial this adapter serves
            max_sequence_staleness: Max events behind for bet validity
        """
        self._data_hub = data_hub
        self._broker = broker
        self._trial_id = trial_id
        self._max_sequence_staleness = max_sequence_staleness

        # Track registered external agents
        self._agents: dict[str, ExternalAgentState] = {}

        # Idempotency tracking for bet deduplication
        self._idempotency_keys: dict[str, str] = {}  # key -> bet_id

        # Trial ended signaling for SSE connections
        self._trial_ended_event = asyncio.Event()
        self._trial_ended_message: TrialEndedMessage | None = None

        logger.info("ExternalAgentAdapter initialized for trial %s", trial_id)

    @property
    def trial_id(self) -> str:
        """Trial ID this adapter serves."""
        return self._trial_id

    # =========================================================================
    # Agent Registration
    # =========================================================================

    async def register_agent(
        self,
        agent_id: str,
        persona: str = "",
        model: str = "",
        initial_balance: str | None = None,
    ) -> AgentRegistrationResponse:
        """Register an external agent.

        Creates account in broker and prepares for subscription.

        Args:
            agent_id: Unique agent identifier
            persona: Agent persona description
            model: Model name/version (stored for future use)
            initial_balance: Starting balance (uses broker default if None)

        Returns:
            Registration response with agent details

        Raises:
            ValueError: If agent already registered
        """
        # model parameter kept for future use (agent metadata tracking)
        _ = model

        if agent_id in self._agents:
            raise ValueError(f"Agent {agent_id} already registered")

        # Use broker's default balance if not specified
        balance = initial_balance or self._broker.initial_balance

        # Create account in broker
        await self._broker.create_account(agent_id, Decimal(balance))

        # Create agent state
        state = ExternalAgentState(agent_id=agent_id)
        self._agents[agent_id] = state

        logger.info(
            "Registered external agent: agent_id=%s, balance=%s, persona=%s",
            agent_id,
            balance,
            persona,
        )

        return AgentRegistrationResponse(
            agent_id=agent_id,
            trial_id=self._trial_id,
            balance=balance,
            registered_at=state.registered_at,
        )

    async def unregister_agent(self, agent_id: str) -> bool:
        """Unregister an external agent.

        Cleans up subscription and removes from tracking.

        Args:
            agent_id: Agent to unregister

        Returns:
            True if agent was found and removed
        """
        state = self._agents.pop(agent_id, None)
        if state is None:
            return False

        # Cleanup subscription
        if state.subscription:
            await self._data_hub.subscription_manager.unsubscribe(
                state.subscription.subscription_id
            )

        logger.info("Unregistered external agent: %s", agent_id)
        return True

    def is_registered(self, agent_id: str) -> bool:
        """Check if agent is registered."""
        return agent_id in self._agents

    # =========================================================================
    # Subscription Management
    # =========================================================================

    async def subscribe(
        self,
        agent_id: str,
        event_types: list[str] | None = None,
        include_snapshot: bool = True,
    ) -> Subscription:
        """Create or get subscription for agent.

        Args:
            agent_id: Agent to subscribe
            event_types: Event type filters (None = all events)
            include_snapshot: Whether to include recent events snapshot

        Returns:
            Subscription for receiving events

        Raises:
            ValueError: If agent not registered
        """
        state = self._agents.get(agent_id)
        if state is None:
            raise ValueError(f"Agent {agent_id} not registered")

        # Create subscription if needed
        if state.subscription is None:
            filters = SubscriptionFilter.from_list(event_types)
            options = SubscriptionOptions(include_snapshot=include_snapshot)

            state.subscription = await self._data_hub.subscription_manager.subscribe(
                subscriber_id=agent_id,
                filters=filters,
                options=options,
            )

            logger.info(
                "Created subscription for agent %s: filters=%s",
                agent_id,
                event_types,
            )

        return state.subscription

    # =========================================================================
    # Odds Queries
    # =========================================================================

    def get_current_odds(self) -> CurrentOddsResponse:
        """Get current betting odds from broker."""
        event = self._broker._event
        current_sequence = self._data_hub.subscription_manager.global_sequence

        if event is None:
            return CurrentOddsResponse(
                event_id="",
                betting_open=False,
                sequence=current_sequence,
            )

        # Convert spread lines
        spread_lines = {}
        for key, val in event.spread_lines.items():
            spread_lines[str(key)] = SpreadLine(
                home_probability=float(val.get("home_probability", 0)),
                away_probability=float(val.get("away_probability", 0)),
            )

        # Convert total lines
        total_lines = {}
        for key, val in event.total_lines.items():
            total_lines[str(key)] = TotalLine(
                over_probability=float(val.get("over_probability", 0)),
                under_probability=float(val.get("under_probability", 0)),
            )

        return CurrentOddsResponse(
            event_id=event.event_id,
            home_probability=(
                float(event.home_probability) if event.home_probability else None
            ),
            away_probability=(
                float(event.away_probability) if event.away_probability else None
            ),
            spread_lines=spread_lines,
            total_lines=total_lines,
            last_update=event.last_odds_update,
            betting_open=event.can_bet,  # type: ignore[arg-type]
            sequence=current_sequence,
        )

    # =========================================================================
    # Betting
    # =========================================================================

    async def place_bet(
        self,
        agent_id: str,
        request: BetRequest,
    ) -> BetResponse:
        """Place a bet on behalf of an external agent.

        Args:
            agent_id: Agent placing the bet
            request: Bet request details

        Returns:
            Bet response with confirmation

        Raises:
            ValueError: For various validation errors
        """
        if not self.is_registered(agent_id):
            raise ValueError(f"Agent {agent_id} not registered")

        # Check idempotency
        if request.idempotency_key:
            existing_bet_id = self._idempotency_keys.get(request.idempotency_key)
            if existing_bet_id:
                # Return existing bet
                bet = self._broker._bets.get(existing_bet_id)
                if bet:
                    return self._bet_to_response(bet)
                raise ValueError("Duplicate idempotency key but bet not found")

        # Check sequence staleness
        if request.reference_sequence is not None:
            current_sequence = self._data_hub.subscription_manager.global_sequence
            staleness = current_sequence - request.reference_sequence
            if staleness > self._max_sequence_staleness:
                raise ValueError(
                    f"Reference sequence too stale: {staleness} > {self._max_sequence_staleness}"
                )

        # Check if betting is open
        if self._broker._event is None or not self._broker._event.can_bet:
            raise ValueError("Betting is closed")

        # Convert to internal bet request
        amount = Decimal(request.amount)
        order_type = (
            OrderType.LIMIT if request.order_type == "limit" else OrderType.MARKET
        )
        limit_prob = (
            Decimal(str(request.limit_probability))
            if request.limit_probability
            else None
        )

        event_id = self._broker._event.event_id

        if request.market == "moneyline":
            if request.selection not in ("home", "away"):
                raise ValueError(
                    f"Invalid selection for moneyline: {request.selection}"
                )
            bet_request = BetRequestMoneyline(
                amount=amount,
                selection=request.selection,  # type: ignore[arg-type]
                event_id=event_id,
                order_type=order_type,
                limit_probability=limit_prob,
            )
        elif request.market == "spread":
            if request.spread_value is None:
                raise ValueError("spread_value required for spread bets")
            if request.selection not in ("home", "away"):
                raise ValueError(f"Invalid selection for spread: {request.selection}")
            bet_request = BetRequestSpread(
                amount=amount,
                selection=request.selection,  # type: ignore[arg-type]
                event_id=event_id,
                order_type=order_type,
                spread_value=Decimal(str(request.spread_value)),
                limit_probability=limit_prob,
            )
        elif request.market == "total":
            if request.total_value is None:
                raise ValueError("total_value required for total bets")
            if request.selection not in ("over", "under"):
                raise ValueError(f"Invalid selection for total: {request.selection}")
            bet_request = BetRequestTotal(
                amount=amount,
                selection=request.selection,  # type: ignore[arg-type]
                event_id=event_id,
                order_type=order_type,
                total_value=Decimal(str(request.total_value)),
                limit_probability=limit_prob,
            )
        else:
            raise ValueError(f"Invalid market type: {request.market}")

        # Place bet through broker
        result = await self._broker.place_bet(agent_id, bet_request)

        # Get the bet that was just placed
        # The broker returns a status string, we need to find the actual bet
        bets = self._get_agent_bets_internal(agent_id)
        if not bets:
            raise ValueError(f"Bet placement returned '{result}' but no bet found")

        # Get the most recent bet (last one placed)
        bet = bets[-1]

        # Track idempotency key
        if request.idempotency_key:
            self._idempotency_keys[request.idempotency_key] = bet.bet_id

        # Update activity timestamp
        self._agents[agent_id].last_activity_at = datetime.now(timezone.utc)

        logger.info(
            "Placed bet for agent %s: bet_id=%s, market=%s, amount=%s",
            agent_id,
            bet.bet_id,
            request.market,
            request.amount,
        )

        return self._bet_to_response(bet)

    def _bet_to_response(self, bet) -> BetResponse:
        """Convert internal Bet to API response."""
        return BetResponse(
            bet_id=bet.bet_id,
            agent_id=bet.agent_id,
            event_id=bet.event_id,
            market=bet.bet_type.value.lower(),
            selection=bet.selection,
            amount=str(bet.amount),
            probability=str(bet.probability),
            shares=str(bet.shares),
            status=bet.status.value.lower(),
            created_at=bet.create_time,
        )

    def _get_agent_bets_internal(self, agent_id: str) -> list:
        """Get all bets for an agent (internal, returns Bet objects)."""
        bet_ids = (
            self._broker._active_bets.get(agent_id, [])
            + self._broker._pending_orders.get(agent_id, [])
            + self._broker._bet_history.get(agent_id, [])
        )

        bets = []
        for bet_id in bet_ids:
            bet = self._broker._bets.get(bet_id)
            if bet:
                bets.append(bet)

        return bets

    # =========================================================================
    # Balance & Bets Queries
    # =========================================================================

    def get_balance(self, agent_id: str) -> BalanceResponse:
        """Get agent's current balance and holdings.

        Args:
            agent_id: Agent to query

        Returns:
            Balance response with holdings

        Raises:
            ValueError: If agent not registered or account not found
        """
        if not self.is_registered(agent_id):
            raise ValueError(f"Agent {agent_id} not registered")

        account = self._broker._accounts.get(agent_id)
        if account is None:
            raise ValueError(f"Account not found for agent {agent_id}")

        # Convert holdings
        holdings = []
        for h in account.holdings:
            holdings.append(
                HoldingResponse(
                    event_id=h.event_id,
                    selection=h.selection,
                    bet_type=h.bet_type.value.lower(),
                    shares=str(h.shares),
                    avg_probability="0",  # Not tracked in Holding model
                    spread_value=str(h.spread_value) if h.spread_value else None,
                    total_value=str(h.total_value) if h.total_value else None,
                )
            )

        return BalanceResponse(
            agent_id=agent_id,
            balance=str(account.balance),
            holdings=holdings,
        )

    def get_bets(self, agent_id: str) -> list[BetResponse]:
        """Get all bets for an agent.

        Args:
            agent_id: Agent to query

        Returns:
            List of bet responses

        Raises:
            ValueError: If agent not registered
        """
        if not self.is_registered(agent_id):
            raise ValueError(f"Agent {agent_id} not registered")

        bets = self._get_agent_bets_internal(agent_id)
        return [self._bet_to_response(bet) for bet in bets]

    # =========================================================================
    # Trial End Signaling
    # =========================================================================

    @property
    def trial_ended_event(self) -> asyncio.Event:
        """Get the trial ended event for SSE connections."""
        return self._trial_ended_event

    def get_trial_ended_message(self) -> TrialEndedMessage | None:
        """Get the trial ended message (if trial has ended)."""
        return self._trial_ended_message

    async def signal_trial_ended(
        self,
        reason: str = "completed",
        message: str = "",
    ) -> None:
        """Signal that the trial has ended.

        This sets the trial_ended_event which will cause all SSE connections
        to send a trial_ended message and close gracefully.

        Args:
            reason: Reason for trial ending ("completed", "cancelled", "failed")
            message: Optional human-readable message
        """
        if self._trial_ended_event.is_set():
            logger.warning("Trial %s already ended, ignoring signal", self._trial_id)
            return

        # Build final results from broker
        final_results = await self._build_final_results()

        self._trial_ended_message = TrialEndedMessage(
            trial_id=self._trial_id,
            reason=reason,
            timestamp=datetime.now(timezone.utc),
            final_results=final_results,
            message=message,
        )

        # Signal all SSE connections
        self._trial_ended_event.set()

        logger.info(
            "Trial %s ended: reason=%s, agents=%d",
            self._trial_id,
            reason,
            len(final_results),
        )

    async def _build_final_results(self) -> list[AgentResult]:
        """Build final results for all agents from broker."""
        results = []

        for agent_id in self._broker._accounts:
            try:
                stats = await self._broker.get_statistics(agent_id)
                account = self._broker._accounts.get(agent_id)
                if account is None:
                    continue

                results.append(
                    AgentResult(
                        agent_id=agent_id,
                        final_balance=str(account.balance),
                        net_profit=str(stats.net_profit),
                        total_bets=stats.total_bets,
                        win_rate=round(stats.win_rate, 4),
                        roi=round(stats.roi, 4),
                    )
                )
            except Exception as e:
                logger.warning("Failed to get results for agent %s: %s", agent_id, e)

        # Sort by balance descending
        results.sort(key=lambda r: float(r.final_balance), reverse=True)
        return results


__all__ = [
    "ExternalAgentAdapter",
    "ExternalAgentState",
]
