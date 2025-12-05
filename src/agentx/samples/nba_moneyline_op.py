"""NBA Moneyline Betting Operator

This module implements a betting broker operator for NBA moneyline betting.
It manages account balances, bet placement, and bet settlement.
"""

import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, TypedDict

from agentx.core import (
    Agent,
    Operator,
    OperatorBase,
    StreamEvent,
)


class BetStatus(Enum):
    """Status of a bet"""

    ACTIVE = "ACTIVE"
    SETTLED = "SETTLED"


class BetOutcome(Enum):
    """Outcome of a settled bet"""

    WIN = "WIN"
    LOSS = "LOSS"


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class Account:
    """Agent account information"""

    agent_id: str
    balance: Decimal
    created_at: datetime
    last_updated: datetime

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "agent_id": self.agent_id,
            "balance": str(self.balance),
            "created_at": self.created_at.isoformat(),
            "last_updated": self.last_updated.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Account":
        """Create from dictionary"""
        return cls(
            agent_id=data["agent_id"],
            balance=Decimal(data["balance"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_updated=datetime.fromisoformat(data["last_updated"]),
        )


@dataclass
class BetRequest:
    """Bet request from agent to broker"""

    amount: Decimal
    selection: str  # e.g., "home" or "away"
    odds: Decimal
    event_id: str

    def validate(self) -> None:
        """Validate bet request parameters"""
        if self.amount <= 0:
            raise ValueError(f"Bet amount must be positive, got {self.amount}")
        if self.odds <= 1.0:
            raise ValueError(f"Odds must be greater than 1.0, got {self.odds}")
        if self.selection not in ["home", "away"]:
            raise ValueError(
                f"Selection must be 'home' or 'away', got {self.selection}"
            )


@dataclass
class Bet:
    """Bet record"""

    bet_id: str
    agent_id: str
    event_id: str
    amount: Decimal
    selection: str
    odds: Decimal
    create_time: datetime
    status: BetStatus
    actual_payout: Optional[Decimal] = None
    outcome: Optional[BetOutcome] = None
    settlement_time: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "bet_id": self.bet_id,
            "agent_id": self.agent_id,
            "event_id": self.event_id,
            "amount": str(self.amount),
            "selection": self.selection,
            "odds": str(self.odds),
            "create_time": self.create_time.isoformat(),
            "status": self.status.value,
            "actual_payout": str(self.actual_payout) if self.actual_payout else None,
            "outcome": self.outcome.value if self.outcome else None,
            "settlement_time": (
                self.settlement_time.isoformat() if self.settlement_time else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bet":
        """Create from dictionary"""
        return cls(
            bet_id=data["bet_id"],
            agent_id=data["agent_id"],
            event_id=data["event_id"],
            amount=Decimal(data["amount"]),
            selection=data["selection"],
            odds=Decimal(data["odds"]),
            create_time=datetime.fromisoformat(data["create_time"]),
            status=BetStatus(data["status"]),
            actual_payout=(
                Decimal(data["actual_payout"]) if data["actual_payout"] else None
            ),
            outcome=BetOutcome(data["outcome"]) if data["outcome"] else None,
            settlement_time=(
                datetime.fromisoformat(data["settlement_time"])
                if data["settlement_time"]
                else None
            ),
        )


@dataclass
class Statistics:
    """Agent performance statistics"""

    total_bets: int
    total_wagered: Decimal
    wins: int
    losses: int
    win_rate: float
    net_profit: Decimal
    roi: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "total_bets": self.total_bets,
            "total_wagered": str(self.total_wagered),
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "net_profit": str(self.net_profit),
            "roi": self.roi,
        }


# =============================================================================
# Configuration
# =============================================================================


class _ActorIdConfig(TypedDict):
    actor_id: str


class BrokerOperatorConfig(_ActorIdConfig, total=False):
    """Configuration for BrokerOperator"""

    initial_balance: str  # the initial balance of all agents (as string for Decimal)


# =============================================================================
# Broker Operator
# =============================================================================


class BrokerOperator(OperatorBase, Operator[BrokerOperatorConfig]):
    """
    Betting Broker Operator for NBA Moneyline Betting.

    Manages:
    - Agent account balances
    - Bet placement and validation
    - Bet settlement based on event results
    - Query functions for agents
    """

    def __init__(self, config: BrokerOperatorConfig):
        super().__init__(config["actor_id"])
        self._agent_registry: Dict[str, Agent] = {}  # agent_id -> Agent

        # Account management
        self._accounts: Dict[str, Account] = {}  # agent_id -> Account
        self._agent_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Bet management
        self._bets: Dict[str, Bet] = {}  # bet_id -> Bet
        self._active_bets: Dict[str, List[str]] = defaultdict(
            list
        )  # agent_id -> [bet_ids]
        self._bet_history: Dict[str, List[str]] = defaultdict(
            list
        )  # agent_id -> [bet_ids]
        self._event_bets: Dict[str, List[str]] = defaultdict(
            list
        )  # event_id -> [bet_ids]

        # Initialize accounts if provided in config
        self.initial_balance = config.get("initial_balance", "0")

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "BrokerOperator":
        # Create typed config
        broker_config: BrokerOperatorConfig = {
            "actor_id": config["actor_id"],
            "initial_balance": config.get("initial_balance", "0"),
        }

        return cls(broker_config)

    async def start(self) -> None:
        """Protocol hook: dashboard calls this before traffic is routed."""
        print(f"[BROKER] Operator '{self.actor_id}' starting")
        return None

    async def stop(self) -> None:
        """Protocol hook: dashboard calls this during shutdown."""
        total_accounts = len(self._accounts)
        total_bets = len(self._bets)
        print(
            f"[BROKER] Operator '{self.actor_id}' stopping - "
            f"accounts={total_accounts}, total_bets={total_bets}"
        )
        return None

    def register_agents(self, agents: Sequence[Agent]) -> None:
        """Register agents that can be notified of stream events."""
        super().register_agents(agents)
        # Create accounts for newly registered agents
        for agent in agents:
            self.create_account(agent.actor_id, Decimal(self.initial_balance))

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Protocol hook: dashboard forwards stream payloads here when routed."""
        try:
            await self.settle_event(event)
        except Exception as e:
            print(f"Error handling event: {e}")

    # =========================================================================
    # Account Management
    # =========================================================================

    def create_account(self, agent_id: str, initial_balance: Decimal) -> Account:
        """Initialize a new agent account."""
        if initial_balance < 0:
            raise ValueError(
                f"Initial balance must be non-negative, got {initial_balance}"
            )

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

        self._log_transaction(agent_id, "ACCOUNT_CREATED", initial_balance)
        return account

    async def get_balance(self, agent_id: str) -> Decimal:
        """Retrieve current account balance."""
        if agent_id not in self._accounts:
            raise ValueError(f"Account not found for agent {agent_id}")

        return self._accounts[agent_id].balance

    async def deposit(self, agent_id: str, amount: Decimal) -> Decimal:
        """
        Add funds to agent account.

        Side Effects:
            - Increases account balance
            - Updates last_updated timestamp
            - Logs transaction
        """
        if amount <= 0:
            raise ValueError(f"Deposit amount must be positive, got {amount}")

        async with self._agent_locks[agent_id]:
            if agent_id not in self._accounts:
                raise ValueError(f"Account not found for agent {agent_id}")

            account = self._accounts[agent_id]
            account.balance += amount
            account.last_updated = datetime.now()

            self._log_transaction(agent_id, "DEPOSIT", amount)
            return account.balance

    async def withdraw(self, agent_id: str, amount: Decimal) -> Decimal:
        """
        Remove funds from agent account.

        Side Effects:
            - Decreases account balance
            - Updates last_updated timestamp
            - Logs transaction
        """
        if amount <= 0:
            raise ValueError(f"Withdrawal amount must be positive, got {amount}")

        async with self._agent_locks[agent_id]:
            if agent_id not in self._accounts:
                raise ValueError(f"Account not found for agent {agent_id}")

            account = self._accounts[agent_id]
            if account.balance < amount:
                raise ValueError(
                    f"Insufficient balance: requested {amount}, available {account.balance}"
                )

            account.balance -= amount
            account.last_updated = datetime.now()

            self._log_transaction(agent_id, "WITHDRAWAL", amount)
            return account.balance

    # =========================================================================
    # Bet Management
    # =========================================================================

    async def place_bet(self, agent_id: str, bet_request: BetRequest) -> Optional[Bet]:
        """
        Accept and process a new bet from an agent.

        Workflow:
            1. Validate bet request
            2. Lock funds from account
            3. Generate unique bet ID
            4. Create bet record
            5. Store in active bets
            6. Notify agent [BET_PLACED, BET_REJECTED]
            7. Log bet placement

        Side Effects:
            - Decreases account balance
            - Adds to active_bets collection
            - Sends notification to agent
            - Creates audit log entry
        """
        try:
            # Validate bet request
            bet_request.validate()

            async with self._agent_locks[agent_id]:
                # Check account exists
                if agent_id not in self._accounts:
                    raise ValueError(f"Account not found for agent {agent_id}")

                account = self._accounts[agent_id]

                # Check sufficient balance
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

                # Create bet record
                bet = Bet(
                    bet_id=bet_id,
                    agent_id=agent_id,
                    event_id=bet_request.event_id,
                    amount=bet_request.amount,
                    selection=bet_request.selection,
                    odds=bet_request.odds,
                    create_time=datetime.now(),
                    status=BetStatus.ACTIVE,
                )

                # Store bet
                self._bets[bet_id] = bet
                self._active_bets[agent_id].append(bet_id)
                self._event_bets[bet_request.event_id].append(bet_id)

                # Log bet placement
                self._log_bet(agent_id, "BET_PLACED", bet)

                # If bet was successfully placed, return the bet
                return bet

        except (ValueError, Exception) as e:
            # If the bet was rejected, return None to the agent
            print(f"Bet rejected for {agent_id}: {e}")
            return None

    async def settle_bet(self, bet: Bet, event: StreamEvent[Any]) -> None:
        """
        Resolve a bet based on event outcome.

        Workflow:
            1. Evaluate bet against result
            2. Calculate payout (if win): gross_payout = bet.amount × bet.odds
            3. Credit account (if win)
            4. Update bet status
            5. Notify agent [BET_WON, BET_LOST]
            6. Log settlement

        Side Effects:
            - Updates account balance (if win)
            - Push the event to the agent
            - Creates settlement log
        """
        async with self._agent_locks[bet.agent_id]:
            # Determine outcome
            winner = event.payload["winner"]
            event_id = event.payload["event_id"]

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

            # Update bet
            bet.status = BetStatus.SETTLED
            bet.outcome = outcome
            bet.actual_payout = payout
            bet.settlement_time = datetime.now()

            # Update collections
            self._active_bets[bet.agent_id].remove(bet.bet_id)
            self._bet_history[bet.agent_id].append(bet.bet_id)

            # Log settlement
            self._log_settlement(bet, event)

            # Push the streamevent to the agent
            print(
                f"[NOTIFICATION] Settling Bet {bet.bet_id} for Agent {bet.agent_id} - "
                f"Event {event_id} (Winner: {winner})"
            )
            """
                We assume a StreamEvent[Any] object has the following attributes:
                - stream_id
                - payload: Dict[str, Any] - The event data containing:
                    - "event_id": str - Unique identifier for the game/event
                    - "winner": str - Result of the game, either "home" or "away"
                    - "final_data": Dict[str, Any] (optional) - Additional game metadata
                - emitted_at: datetime - Timestamp when the event was emitted
            """

            await self._notify_agent(bet.agent_id, event)

    async def settle_event(self, event: StreamEvent[Any]) -> int:
        """Settle all bets for a given event."""
        event_id = event.payload["event_id"]
        bet_ids = self._event_bets.get(event_id, []).copy()

        for bet_id in bet_ids:
            bet = self._bets[bet_id]
            if bet.status == BetStatus.ACTIVE:
                await self.settle_bet(bet, event)

        return len(bet_ids)

    # =========================================================================
    # Query Functions
    # =========================================================================

    async def get_active_bets(self, agent_id: str) -> List[Bet]:
        """
        Retrieve all active bets for an agent.
        """
        bet_ids = self._active_bets.get(agent_id, [])
        return [self._bets[bet_id] for bet_id in bet_ids]

    async def get_bet_history(self, agent_id: str, limit: int = 100) -> List[Bet]:
        """
        Retrieve settled bet history.
        """
        bet_ids = self._bet_history.get(agent_id, [])
        # Return most recent first
        recent_bet_ids = reversed(bet_ids[-limit:])
        return [self._bets[bet_id] for bet_id in recent_bet_ids]

    async def get_statistics(self, agent_id: str) -> Statistics:
        """
        Calculate performance metrics for an agent.
        """
        all_bet_ids = self._active_bets.get(agent_id, []) + self._bet_history.get(
            agent_id, []
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

    async def get_account(self, agent_id: str) -> Account:
        """
        Get account information for an agent.
        """
        if agent_id not in self._accounts:
            raise ValueError(f"Account not found for agent {agent_id}")

        return self._accounts[agent_id]

    # =========================================================================
    # State Management
    # =========================================================================

    def save_state(self) -> Dict[str, Any]:
        """
        Export operator state for persistence.
        """
        return {
            "actor_id": self.actor_id,
            "accounts": {
                agent_id: account.to_dict()
                for agent_id, account in self._accounts.items()
            },
            "bets": {bet_id: bet.to_dict() for bet_id, bet in self._bets.items()},
            "active_bets": dict(self._active_bets),
            "bet_history": dict(self._bet_history),
            "event_bets": dict(self._event_bets),
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """
        Import operator state from persistence.
        """

        # Load accounts
        self._accounts = {
            agent_id: Account.from_dict(account_data)
            for agent_id, account_data in state["accounts"].items()
        }

        # Load bets
        self._bets = {
            bet_id: Bet.from_dict(bet_data)
            for bet_id, bet_data in state["bets"].items()
        }

        # Load collections
        self._active_bets = defaultdict(list, state["active_bets"])
        self._bet_history = defaultdict(list, state["bet_history"])
        self._event_bets = defaultdict(list, state["event_bets"])

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _log_transaction(
        self, agent_id: str, transaction_type: str, amount: Decimal
    ) -> None:
        """Log account transaction"""
        # In production, this would write to a structured log or database
        print(
            f"[TRANSACTION] {datetime.now().isoformat()} - "
            f"Agent: {agent_id}, Type: {transaction_type}, Amount: {amount}"
        )

    def _log_bet(self, agent_id: str, action: str, bet: Bet) -> None:
        """Log bet action"""
        print(
            f"[BET] {datetime.now().isoformat()} - "
            f"Agent: {agent_id}, Action: {action}, "
            f"BetID: {bet.bet_id}, Amount: {bet.amount}, "
            f"Selection: {bet.selection}, Odds: {bet.odds}"
        )

    def _log_settlement(self, bet: Bet, event: StreamEvent[Any]) -> None:
        """Log bet settlement"""
        event_id = event.payload["event_id"]
        winner = event.payload["winner"]
        outcome_str = bet.outcome.value if bet.outcome else "UNKNOWN"
        print(
            f"[SETTLEMENT] {datetime.now().isoformat()} - "
            f"BetID: {bet.bet_id}, EventID: {event_id}, "
            f"Winner: {winner}, Outcome: {outcome_str}, "
            f"Payout: {bet.actual_payout}"
        )
