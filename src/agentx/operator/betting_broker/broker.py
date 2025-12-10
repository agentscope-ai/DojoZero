"""Betting Broker Operator

This module implements the betting broker operator that manages:
- Account balances
- Event lifecycle (pregame, odds updates, game start/end, settlement)
- Bet placement and execution (market and limit orders)
- Bet settlement
"""

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Sequence, Set, TypedDict

from agentx.core import (
    Agent,
    Operator,
    OperatorBase,
    StreamEvent,
)

from .model import (
    Account,
    Bet,
    BetRequest,
    BettingPhase,
    BetStatus,
    BetOutcome,
    Event,
    EventStatus,
    OrderType,
    Statistics,
)


# =============================================================================
# Configuration
# =============================================================================


class _ActorIdConfig(TypedDict):
    actor_id: str


class BrokerOperatorConfig(_ActorIdConfig, total=False):
    """Configuration for BrokerOperator"""

    initial_balance: str  # Initial balance for all agents (as string for Decimal)


# =============================================================================
# Broker Operator
# =============================================================================


class BrokerOperator(OperatorBase, Operator[BrokerOperatorConfig]):
    """
    Betting Broker Operator for Sports Betting.

    Manages:
    - Agent account balances
    - Event lifecycle (pregame → live → closed → settled)
    - Bet placement (market and limit orders)
    - Order matching and execution
    - Bet settlement based on event results
    """

    def __init__(self, config: BrokerOperatorConfig):
        super().__init__(config["actor_id"])

        # Account management
        self._accounts: Dict[str, Account] = {}
        self._agent_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Event management
        self._events: Dict[str, Event] = {}
        self._event_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Bet management
        self._bets: Dict[str, Bet] = {}

        # Agent-indexed bet tracking
        self._active_bets: Dict[str, List[str]] = defaultdict(list)
        self._pending_orders: Dict[str, List[str]] = defaultdict(list)
        self._bet_history: Dict[str, List[str]] = defaultdict(list)

        # Event-indexed bet tracking
        self._event_active_bets: Dict[str, Set[str]] = defaultdict(set)
        self._event_pending_orders: Dict[str, Set[str]] = defaultdict(set)

        # Configuration
        self.initial_balance = config.get("initial_balance", "0")

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "BrokerOperator":
        """Create broker from configuration dictionary"""
        broker_config: BrokerOperatorConfig = {
            "actor_id": config["actor_id"],
            "initial_balance": config.get("initial_balance", "0"),
        }
        return cls(broker_config)

    async def start(self) -> None:
        """Protocol hook: called before traffic is routed"""
        print(f"[BROKER] Operator '{self.actor_id}' starting")

    async def stop(self) -> None:
        """Protocol hook: called during shutdown"""
        print(
            f"[BROKER] Operator '{self.actor_id}' stopping - "
            f"accounts={len(self._accounts)}, events={len(self._events)}, "
            f"bets={len(self._bets)}"
        )

    def register_agents(self, agents: Sequence[Agent]) -> None:
        """Register agents and create their accounts"""
        super().register_agents(agents)
        for agent in agents:
            if agent.actor_id not in self._accounts:
                self.create_account(agent.actor_id, Decimal(self.initial_balance))

    # =========================================================================
    # Event Stream Processing
    # =========================================================================

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process incoming stream events and delegate to appropriate handlers"""
        try:
            payload = event.payload
            event_id = payload.get("event_id")

            if not event_id:
                print(f"[ERROR] Event missing event_id: {payload}")
                return

            async with self._event_locks[
                event_id
            ]:  # avoid multiple streamEvent change the same event
                event_type = payload.get("type")

                if event_type == "pregame":
                    await self.initialize_event(
                        event_id=payload["event_id"],
                        home_team=payload["home_team"],
                        away_team=payload["away_team"],
                        game_time=datetime.fromisoformat(payload["game_time"]),
                        initial_home_odds=Decimal(str(payload["initial_home_odds"])),
                        initial_away_odds=Decimal(str(payload["initial_away_odds"])),
                    )

                elif event_type == "odds_update":
                    await self.update_odds(
                        event_id=payload["event_id"],
                        home_odds=Decimal(str(payload["home_odds"])),
                        away_odds=Decimal(str(payload["away_odds"])),
                    )

                elif event_type == "game_start":
                    await self.update_event_status(
                        event_id=payload["event_id"], status=EventStatus.LIVE
                    )

                elif event_type == "game_result":
                    await self.update_event_status(
                        event_id=payload["event_id"], status=EventStatus.CLOSED
                    )
                    await self.settle_event(
                        event_id=payload["event_id"],
                        winner=payload["winner"],
                        final_score=payload["final_score"],
                    )

                else:
                    print(f"[WARNING] Unknown event type: {event_type}")

        except Exception as e:
            print(f"[ERROR] Failed to handle stream event: {e}")

    async def initialize_event(
        self,
        event_id: str,
        home_team: str,
        away_team: str,
        game_time: datetime,
        initial_home_odds: Decimal,
        initial_away_odds: Decimal,
    ) -> Event:
        """Initialize a new betting event with starting odds"""
        if event_id in self._events:
            raise ValueError(f"Event {event_id} already exists")

        if initial_home_odds <= 1.0 or initial_away_odds <= 1.0:
            raise ValueError("Odds must be greater than 1.0")

        now = datetime.now()
        event = Event(
            event_id=event_id,
            home_team=home_team,
            away_team=away_team,
            game_time=game_time,
            status=EventStatus.SCHEDULED,
            home_odds=initial_home_odds,
            away_odds=initial_away_odds,
            last_odds_update=now,
        )

        self._events[event_id] = event
        print(
            f"[EVENT] Created {event_id}: {home_team} vs {away_team} "
            f"(Odds: {initial_home_odds}/{initial_away_odds})"
        )
        return event

    async def update_odds(
        self, event_id: str, home_odds: Decimal, away_odds: Decimal
    ) -> Event:
        """Update odds for an event and execute matching limit orders"""
        if event_id not in self._events:
            raise ValueError(f"Event {event_id} not found")

        event = self._events[event_id]

        if event.status not in [EventStatus.SCHEDULED, EventStatus.LIVE]:
            raise ValueError(f"Cannot update odds for {event.status.value} event")

        if home_odds <= 1.0 or away_odds <= 1.0:
            raise ValueError("Odds must be greater than 1.0")

        # Update event odds
        event.home_odds = home_odds
        event.away_odds = away_odds
        event.last_odds_update = datetime.now()

        print(
            f"[ODDS] Updated {event_id}: home={home_odds}, away={away_odds} "
            f"({event.status.value})"
        )

        # Check and execute matching limit orders
        pending_bet_ids = list(self._event_pending_orders.get(event_id, set()))

        for bet_id in pending_bet_ids:
            bet = self._bets[bet_id]

            # Determine if limit order should execute
            should_execute = False
            execution_odds = Decimal(0)
            if bet.limit_odds is not None:
                if bet.selection == "home" and home_odds >= bet.limit_odds:
                    should_execute = True
                    execution_odds = home_odds
                elif bet.selection == "away" and away_odds >= bet.limit_odds:
                    should_execute = True
                    execution_odds = away_odds

            if should_execute:
                await self.match_bet(bet, execution_odds)

        return event

    async def update_event_status(self, event_id: str, status: EventStatus) -> None:
        """Update event status and perform status-specific actions"""
        if event_id not in self._events:
            raise ValueError(f"Event {event_id} not found")

        event = self._events[event_id]

        # Validate status transition
        if event.status == EventStatus.SETTLED:
            raise ValueError("Cannot change status of settled event")

        print(f"[EVENT] {event_id} status: {event.status.value} → {status.value}")

        event.status = status

        if status == EventStatus.LIVE:
            # Game started - reject pre-game bets and cancel unfilled pre-game orders
            event.betting_closed_at = datetime.now()
            await self._cancel_pregame_orders(event_id)

        elif status == EventStatus.CLOSED:
            # Game ended - reject all bets and cancel all pending orders
            event.betting_closed_at = datetime.now()
            await self._cancel_all_pending_orders(event_id)

    async def settle_event(
        self, event_id: str, winner: str, final_score: Dict[str, int]
    ) -> None:
        """Settle all active bets for a completed event"""
        if event_id not in self._events:
            raise ValueError(f"Event {event_id} not found")

        event = self._events[event_id]

        if event.status != EventStatus.CLOSED:
            raise ValueError(
                f"Cannot settle event with status {event.status.value}, "
                f"must be CLOSED"
            )

        if winner not in ["home", "away"]:
            raise ValueError(f"Invalid winner: {winner}")

        print(
            f"[SETTLEMENT] Settling {event_id} - Winner: {winner}, "
            f"Score: {final_score}"
        )

        # Get all active bets for this event
        active_bet_ids = list(self._event_active_bets.get(event_id, set()))

        # Settle each bet
        settled_count = 0
        for bet_id in active_bet_ids:
            bet = self._bets[bet_id]
            if bet.status == BetStatus.ACTIVE:
                await self._settle_bet(bet, winner)
                settled_count += 1

        # Update event status
        event.status = EventStatus.SETTLED

        print(f"[SETTLEMENT] Completed {event_id} - Settled {settled_count} bets")

    async def _settle_bet(self, bet: Bet, winner: str) -> None:
        """Settle a single bet"""
        async with self._agent_locks[bet.agent_id]:
            # Determine outcome
            is_win = bet.selection == winner
            outcome = BetOutcome.WIN if is_win else BetOutcome.LOSS

            # Calculate payout
            payout = Decimal(0)
            if is_win:
                payout = bet.amount * bet.odds
                # Credit account
                account = self._accounts[bet.agent_id]
                account.balance += payout
                account.last_updated = datetime.now()

            # Update bet record
            bet.status = BetStatus.SETTLED
            bet.outcome = outcome
            bet.actual_payout = payout
            bet.settlement_time = datetime.now()

            # Update collections
            self._active_bets[bet.agent_id].remove(bet.bet_id)
            self._bet_history[bet.agent_id].append(bet.bet_id)
            self._event_active_bets[bet.event_id].discard(bet.bet_id)

            # Log settlement
            print(
                f"[SETTLEMENT] Bet {bet.bet_id} - Agent {bet.agent_id}: "
                f"{outcome.value}, Payout: {payout}"
            )

            # Notify agent
            notification = StreamEvent(
                stream_id=f"settlement_{bet.bet_id}",
                payload={
                    "type": "bet_settled",
                    "bet_id": bet.bet_id,
                    "agent_id": bet.agent_id,
                    "event_id": bet.event_id,
                    "outcome": outcome.value,
                    "payout": str(payout),
                    "winner": winner,
                },
                emitted_at=datetime.now(),
            )
            await self._notify_agent(bet.agent_id, notification)

    async def _cancel_pregame_orders(self, event_id: str) -> None:
        """Cancel all unfilled pre-game orders for an event"""
        pending_bet_ids = list(self._event_pending_orders.get(event_id, set()))

        cancelled_count = 0
        for bet_id in pending_bet_ids:
            bet = self._bets[bet_id]
            if bet.betting_phase == BettingPhase.PRE_GAME:
                await self._cancel_pending_order(bet)
                cancelled_count += 1

        if cancelled_count > 0:
            print(f"[EVENT] Cancelled {cancelled_count} pre-game orders for {event_id}")

    async def _cancel_all_pending_orders(self, event_id: str) -> None:
        """Cancel all pending orders for an event"""
        pending_bet_ids = list(self._event_pending_orders.get(event_id, set()))

        for bet_id in pending_bet_ids:
            bet = self._bets[bet_id]
            await self._cancel_pending_order(bet)

        if pending_bet_ids:
            print(
                f"[EVENT] Cancelled {len(pending_bet_ids)} pending orders "
                f"for {event_id}"
            )

    async def _cancel_pending_order(self, bet: Bet) -> None:
        """Cancel a pending order and refund"""
        async with self._agent_locks[bet.agent_id]:
            # Refund locked funds
            account = self._accounts[bet.agent_id]
            account.balance += bet.amount
            account.last_updated = datetime.now()

            # Update bet status
            bet.status = BetStatus.CANCELLED

            # Remove from collections
            self._pending_orders[bet.agent_id].remove(bet.bet_id)
            self._event_pending_orders[bet.event_id].discard(bet.bet_id)
            self._bet_history[bet.agent_id].append(bet.bet_id)

            print(
                f"[CANCEL] Bet {bet.bet_id} cancelled - "
                f"Refunded {bet.amount} to {bet.agent_id}"
            )

    # =========================================================================
    # Account Management
    # =========================================================================

    def create_account(self, agent_id: str, initial_balance: Decimal) -> Account:
        """Initialize a new agent account"""
        if initial_balance < 0:
            raise ValueError("Initial balance must be non-negative")

        if agent_id in self._accounts:
            raise ValueError(f"Account for agent {agent_id} already exists")

        now = datetime.now()
        account = Account(
            agent_id=agent_id,
            balance=initial_balance,
            created_at=now,
            last_updated=now,
        )
        self._accounts[agent_id] = account

        print(f"[ACCOUNT] Created for {agent_id} with balance {initial_balance}")
        return account

    async def get_balance(self, agent_id: str) -> Decimal:
        """Retrieve current account balance"""
        if agent_id not in self._accounts:
            raise ValueError(f"Account not found for agent {agent_id}")
        return self._accounts[agent_id].balance

    async def deposit(self, agent_id: str, amount: Decimal) -> Decimal:
        """Add funds to agent account"""
        if amount <= 0:
            raise ValueError("Deposit amount must be positive")

        async with self._agent_locks[agent_id]:
            if agent_id not in self._accounts:
                raise ValueError(f"Account not found for agent {agent_id}")

            account = self._accounts[agent_id]
            account.balance += amount
            account.last_updated = datetime.now()

            print(f"[DEPOSIT] {agent_id}: +{amount} (balance: {account.balance})")
            return account.balance

    async def withdraw(self, agent_id: str, amount: Decimal) -> Decimal:
        """Remove funds from agent account"""
        if amount <= 0:
            raise ValueError("Withdrawal amount must be positive")

        async with self._agent_locks[agent_id]:
            if agent_id not in self._accounts:
                raise ValueError(f"Account not found for agent {agent_id}")

            account = self._accounts[agent_id]
            if account.balance < amount:
                raise ValueError(
                    f"Insufficient balance: requested {amount}, "
                    f"available {account.balance}"
                )

            account.balance -= amount
            account.last_updated = datetime.now()

            print(f"[WITHDRAW] {agent_id}: -{amount} (balance: {account.balance})")
            return account.balance

    # =========================================================================
    # Bet Management
    # =========================================================================

    async def get_quote(self, event_id: str) -> Dict[str, Any]:
        """Get current odds for an event.

        Returns a dictionary with event information for easy parsing by agents.

        Returns:
            Dictionary with event details:
            {
                "event_id": str,
                "home_team": str,
                "away_team": str,
                "game_time": str (ISO format),
                "status": str,
                "home_odds": str (Decimal as string),
                "away_odds": str (Decimal as string),
                "last_odds_update": str (ISO format),
                "betting_closed_at": str (ISO format) or None
            }

        Raises:
            ValueError: If event not found
        """
        async with self._event_locks[event_id]:
            if event_id not in self._events:
                raise ValueError(f"Event {event_id} not found")
            return self._events[event_id].to_dict()

    async def place_bet(self, agent_id: str, bet_request: BetRequest) -> str:
        """Place a new bet (synchronous confirmation).

        Returns:
            "bet_placed" - Bet successfully placed (funds locked)
            "bet_invalid" - Bet rejected due to validation error
        """
        try:
            # Validate bet request
            bet_request.validate()

            async with self._agent_locks[agent_id]:
                # Check event exists
                if bet_request.event_id not in self._events:
                    raise ValueError(f"Event {bet_request.event_id} not found")

                event = self._events[bet_request.event_id]

                # Check event is accepting bets
                if event.status == EventStatus.CLOSED:
                    raise ValueError("Event is closed for betting")
                if event.status == EventStatus.SETTLED:
                    raise ValueError("Event has been settled")

                # Validate betting phase matches event status
                if bet_request.betting_phase == BettingPhase.PRE_GAME:
                    if event.status != EventStatus.SCHEDULED:
                        raise ValueError(
                            "Pre-game betting only allowed for scheduled events"
                        )
                elif bet_request.betting_phase == BettingPhase.IN_GAME:
                    if event.status != EventStatus.LIVE:
                        raise ValueError("In-game betting only allowed for live events")

                # Check account exists and has sufficient balance
                if agent_id not in self._accounts:
                    raise ValueError(f"Account not found for agent {agent_id}")

                account = self._accounts[agent_id]
                if account.balance < bet_request.amount:
                    raise ValueError(
                        f"Insufficient balance: requested {bet_request.amount}, "
                        f"available {account.balance}"
                    )

                # Lock funds
                account.balance -= bet_request.amount
                account.last_updated = datetime.now()

                # Generate bet ID
                bet_id = str(uuid.uuid4())

                # Determine execution odds for market orders
                execution_odds = (
                    event.home_odds
                    if bet_request.selection == "home"
                    else event.away_odds
                )

                # Create bet record
                bet = Bet(
                    bet_id=bet_id,
                    agent_id=agent_id,
                    event_id=bet_request.event_id,
                    amount=bet_request.amount,
                    selection=bet_request.selection,
                    odds=execution_odds,  # Will be updated for limit orders
                    order_type=bet_request.order_type,
                    limit_odds=bet_request.limit_odds,
                    betting_phase=bet_request.betting_phase,
                    create_time=datetime.now(),
                    execution_time=None,  # Set by match_bet
                    status=BetStatus.PENDING,  # Will be updated by match_bet
                )

                # Store bet
                self._bets[bet_id] = bet

                # Process based on order type
                if bet_request.order_type == OrderType.MARKET:
                    # Execute immediately
                    await self.match_bet(bet, execution_odds)
                else:
                    # Add to pending orders (order book)
                    self._pending_orders[agent_id].append(bet_id)
                    self._event_pending_orders[bet_request.event_id].add(bet_id)
                    print(
                        f"[BET] Limit order placed - {bet_id}: "
                        f"{agent_id} ${bet_request.amount} on "
                        f"{bet_request.selection} @ {bet_request.limit_odds}+"
                    )

                return "bet_placed"

        except (ValueError, Exception) as e:
            print(f"[ERROR] Bet rejected for {agent_id}: {e}")
            return "bet_invalid"

    async def match_bet(self, bet: Bet, execution_odds: Decimal) -> None:
        """Execute a bet at specified odds (asynchronous notification)"""
        # Update bet record
        bet.odds = execution_odds
        bet.execution_time = datetime.now()
        bet.status = BetStatus.ACTIVE

        # Move from pending to active
        if bet.bet_id in self._pending_orders[bet.agent_id]:
            self._pending_orders[bet.agent_id].remove(bet.bet_id)
        if bet.bet_id in self._event_pending_orders[bet.event_id]:
            self._event_pending_orders[bet.event_id].discard(bet.bet_id)

        self._active_bets[bet.agent_id].append(bet.bet_id)
        self._event_active_bets[bet.event_id].add(bet.bet_id)

        print(
            f"[EXECUTE] Bet {bet.bet_id} executed - "
            f"{bet.agent_id} ${bet.amount} on {bet.selection} @ {execution_odds}"
        )

        # Send execution notification to agent
        notification = StreamEvent(
            stream_id=f"execution_{bet.bet_id}",
            payload={
                "type": "bet_executed",
                "bet_id": bet.bet_id,
                "agent_id": bet.agent_id,
                "event_id": bet.event_id,
                "selection": bet.selection,
                "amount": str(bet.amount),
                "execution_odds": str(execution_odds),
                "execution_time": bet.execution_time.isoformat(),
            },
            emitted_at=datetime.now(),
        )
        await self._notify_agent(bet.agent_id, notification)

    async def cancel_bet(self, agent_id: str, bet_id: str) -> str:
        """Cancel a pending limit order and refund locked funds.

        Returns:
            "bet_cancelled" - Bet successfully cancelled and funds refunded
            "cancel_failed" - Cancellation failed (see logs for reason)
        """
        if bet_id not in self._bets:
            print(f"[CANCEL_FAILED] Bet {bet_id} not found")
            return "cancel_failed"

        bet = self._bets[bet_id]

        if bet.agent_id != agent_id:
            print(f"[CANCEL_FAILED] Bet {bet_id} does not belong to agent {agent_id}")
            return "cancel_failed"

        if bet.status != BetStatus.PENDING:
            print(f"[CANCEL_FAILED] Bet {bet_id} is {bet.status.value}, cannot cancel")
            return "cancel_failed"

        async with self._agent_locks[agent_id]:
            await self._cancel_pending_order(bet)
            return "bet_cancelled"

    # =========================================================================
    # Query Functions
    # =========================================================================

    async def get_active_bets(self, agent_id: str) -> List[Bet]:
        """Retrieve all active bets (executed, not settled)"""
        bet_ids = self._active_bets.get(agent_id, [])
        return [self._bets[bet_id] for bet_id in bet_ids]

    async def get_pending_orders(self, agent_id: str) -> List[Bet]:
        """Retrieve all pending limit orders (not yet executed)"""
        bet_ids = self._pending_orders.get(agent_id, [])
        return [self._bets[bet_id] for bet_id in bet_ids]

    async def get_bet_history(self, agent_id: str, limit: int = 100) -> List[Bet]:
        """Retrieve settled bet history"""
        bet_ids = self._bet_history.get(agent_id, [])
        recent_bet_ids = reversed(bet_ids[-limit:])
        return [self._bets[bet_id] for bet_id in recent_bet_ids]

    async def get_statistics(self, agent_id: str) -> Statistics:
        """Calculate performance metrics for an agent"""
        all_bet_ids = (
            self._active_bets.get(agent_id, [])
            + self._pending_orders.get(agent_id, [])
            + self._bet_history.get(agent_id, [])
        )

        if not all_bet_ids:
            return Statistics(
                total_bets=0,
                total_wagered=Decimal(0),
                wins=0,
                losses=0,
                win_rate=0.0,
                net_profit=Decimal(0),
                roi=0.0,
            )

        total_bets = len(all_bet_ids)
        total_wagered = Decimal(0)
        wins = 0
        losses = 0
        total_won = Decimal(0)
        total_lost = Decimal(0)

        for bet_id in all_bet_ids:
            bet = self._bets[bet_id]
            total_wagered += bet.amount

            if bet.status == BetStatus.SETTLED:
                if bet.outcome == BetOutcome.WIN:
                    wins += 1
                    total_won += bet.actual_payout or Decimal(0)
                elif bet.outcome == BetOutcome.LOSS:
                    losses += 1
                    total_lost += bet.amount

        # Calculate metrics
        settled_bets = wins + losses
        win_rate = wins / settled_bets if settled_bets > 0 else 0.0
        net_profit = total_won - total_lost
        roi = float(net_profit / total_wagered) if total_wagered > 0 else 0.0

        return Statistics(
            total_bets=total_bets,
            total_wagered=total_wagered,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            net_profit=net_profit,
            roi=roi,
        )

    async def get_available_events(self) -> List[Event]:
        """Get all events currently accepting bets"""
        return [
            event
            for event in self._events.values()
            if event.status
            in [EventStatus.SCHEDULED, EventStatus.LIVE]  # Both accept bets
        ]

    async def get_account(self, agent_id: str) -> Account:
        """Get account information for an agent"""
        if agent_id not in self._accounts:
            raise ValueError(f"Account not found for agent {agent_id}")
        return self._accounts[agent_id]

    # =========================================================================
    # State Management
    # =========================================================================

    def save_state(self) -> Dict[str, Any]:
        """Export operator state for persistence"""
        return {
            "actor_id": self.actor_id,
            "accounts": {
                agent_id: account.to_dict()
                for agent_id, account in self._accounts.items()
            },
            "events": {
                event_id: event.to_dict() for event_id, event in self._events.items()
            },
            "bets": {bet_id: bet.to_dict() for bet_id, bet in self._bets.items()},
            "active_bets": dict(self._active_bets),
            "pending_orders": dict(self._pending_orders),
            "bet_history": dict(self._bet_history),
            "event_active_bets": {
                k: list(v) for k, v in self._event_active_bets.items()
            },
            "event_pending_orders": {
                k: list(v) for k, v in self._event_pending_orders.items()
            },
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Import operator state from persistence"""
        # Load accounts
        self._accounts = {
            agent_id: Account.from_dict(account_data)
            for agent_id, account_data in state["accounts"].items()
        }

        # Load events
        self._events = {
            event_id: Event.from_dict(event_data)
            for event_id, event_data in state["events"].items()
        }

        # Load bets
        self._bets = {
            bet_id: Bet.from_dict(bet_data)
            for bet_id, bet_data in state["bets"].items()
        }

        # Load collections
        self._active_bets = defaultdict(list, state["active_bets"])
        self._pending_orders = defaultdict(list, state["pending_orders"])
        self._bet_history = defaultdict(list, state["bet_history"])
        self._event_active_bets = defaultdict(
            set, {k: set(v) for k, v in state["event_active_bets"].items()}
        )
        self._event_pending_orders = defaultdict(
            set, {k: set(v) for k, v in state["event_pending_orders"].items()}
        )

    # =========================================================================
    # Agent Tools
    # =========================================================================
    def agent_tools(
        self, agent_id: str, operator: "BrokerOperator | None" = None
    ) -> list:
        """Return tool functions bound to agent_id for toolkit registration.

        Args:
            agent_id: The agent ID to bind tools to
            operator: Optional broker reference (e.g. Ray proxy) for tools to use.
                      If None, uses self. Pass Ray proxy for distributed execution.

        Returns:
            List of tool functions for agent to use
        """
        from agentx.agents.toolkit import tool  # type: ignore[import-untyped]

        target = operator if operator is not None else self

        @tool
        async def get_balance() -> str:
            """Get current account balance.

            Returns:
                Current balance as string (e.g., "1000.00")
            """
            balance = await target.get_balance(agent_id)
            return str(balance)

        @tool
        async def get_quote(event_id: str) -> str:
            """Get current odds for an event.

            Args:
                event_id: The event to get odds for

            Returns:
                JSON string with event details including odds, teams, status
            """
            import json

            quote = await target.get_quote(event_id)
            return json.dumps(quote)

        @tool
        async def place_bet(
            amount: str,
            selection: str,
            event_id: str,
            order_type: str = "MARKET",
            betting_phase: str = "PRE_GAME",
            limit_odds: str | None = None,
        ) -> str:
            """Place a bet on an event.

            Args:
                amount: Bet amount (e.g., "100.00")
                selection: "home" or "away"
                event_id: Event to bet on
                order_type: "MARKET" (immediate) or "LIMIT" (conditional), default "MARKET"
                betting_phase: "PRE_GAME" or "IN_GAME", default "PRE_GAME"
                limit_odds: Minimum odds for LIMIT orders (e.g., "2.00"), required for LIMIT

            Returns:
                "bet_placed" if successful, "bet_invalid" if rejected
            """
            from .model import BetRequest, OrderType, BettingPhase

            bet_request = BetRequest(
                amount=Decimal(amount),
                selection=selection,
                event_id=event_id,
                order_type=OrderType[order_type],
                betting_phase=BettingPhase[betting_phase],
                limit_odds=Decimal(limit_odds) if limit_odds else None,
            )
            result = await target.place_bet(agent_id, bet_request)
            return result

        @tool
        async def cancel_bet(bet_id: str) -> str:
            """Cancel a pending limit order.

            Args:
                bet_id: ID of the bet to cancel

            Returns:
                "bet_cancelled" if successful, "cancel_failed" if failed
            """
            result = await target.cancel_bet(agent_id, bet_id)
            return result

        @tool
        async def get_active_bets() -> str:
            """Get all active bets (executed, waiting for settlement).

            Returns:
                JSON string with list of active bets
            """
            import json

            bets = await target.get_active_bets(agent_id)
            return json.dumps([bet.to_dict() for bet in bets])

        @tool
        async def get_pending_orders() -> str:
            """Get all pending limit orders (not yet executed).

            Returns:
                JSON string with list of pending orders
            """
            import json

            orders = await target.get_pending_orders(agent_id)
            return json.dumps([order.to_dict() for order in orders])

        @tool
        async def get_bet_history(limit: int = 20) -> str:
            """Get bet history (settled bets).

            Args:
                limit: Maximum number of bets to return (default 20)

            Returns:
                JSON string with list of historical bets
            """
            import json

            history = await target.get_bet_history(agent_id, limit)
            return json.dumps([bet.to_dict() for bet in history])

        @tool
        async def get_statistics() -> str:
            """Get performance statistics.

            Returns:
                JSON string with stats: total_bets, wins, losses, win_rate, net_profit, roi
            """
            import json

            stats = await target.get_statistics(agent_id)
            return json.dumps(stats.to_dict())

        @tool
        async def get_available_events() -> str:
            """Get all events currently accepting bets.

            Returns:
                JSON string with list of available events
            """
            import json

            events = await target.get_available_events()
            return json.dumps([event.to_dict() for event in events])

        return [
            get_balance,
            get_quote,
            place_bet,
            cancel_bet,
            get_active_bets,
            get_pending_orders,
            get_bet_history,
            get_statistics,
            get_available_events,
        ]
