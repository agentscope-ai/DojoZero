"""Betting domain models — accounts, bets, events, statistics, and enums.

Extracted from ``_broker.py`` so that consumers can import lightweight data
contracts without pulling in the full broker operator implementation.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, Literal, Optional, Union

from pydantic import BaseModel, Field, computed_field


# =============================================================================
# Enums
# =============================================================================


class EventStatus(Enum):
    """Status of a betting event"""

    SCHEDULED = "SCHEDULED"  # Pre-game, accepting bets
    LIVE = "LIVE"  # In-game, accepting live bets only
    CLOSED = "CLOSED"  # Game ended, no more bets
    SETTLED = "SETTLED"  # All bets settled


# Valid status transitions for betting events
VALID_STATUS_TRANSITIONS = {
    EventStatus.SCHEDULED: {EventStatus.LIVE, EventStatus.CLOSED},
    EventStatus.LIVE: {EventStatus.CLOSED},
    EventStatus.CLOSED: {EventStatus.SETTLED},
    EventStatus.SETTLED: set(),
}


class OrderType(Enum):
    """Type of bet order"""

    MARKET = "MARKET"  # Execute immediately at current odds
    LIMIT = "LIMIT"  # Execute only when odds reach limit_odds


class BettingPhase(Enum):
    """Phase when bet is placed"""

    PRE_GAME = "PRE_GAME"  # Before game starts
    IN_GAME = "IN_GAME"  # During game (live betting)


class BetStatus(Enum):
    """Status of a bet"""

    PENDING = "PENDING"  # Limit order waiting for execution
    ACTIVE = "ACTIVE"  # Executed, waiting for settlement
    SETTLED = "SETTLED"  # Completed with outcome
    CANCELLED = "CANCELLED"  # Cancelled before execution


class BetOutcome(Enum):
    """Outcome of a settled bet"""

    WIN = "WIN"
    LOSS = "LOSS"


class BetType(Enum):
    """Type of bet"""

    MONEYLINE = "MONEYLINE"  # Default: win/lose
    SPREAD = "SPREAD"  # Point spread
    TOTAL = "TOTAL"  # Over/under total points


# =============================================================================
# Account
# =============================================================================


class Account(BaseModel):
    """Agent account information"""

    agent_id: str
    balance: Decimal
    created_at: datetime
    last_updated: datetime


# =============================================================================
# Event
# =============================================================================


class BettingEvent(BaseModel):
    """Betting event (e.g., a sports game)"""

    event_id: str
    home_team: str
    away_team: str
    game_time: datetime
    status: EventStatus
    home_odds: Optional[Decimal] = Field(
        default=None, description="Can be None initially, filled in when odds arrive"
    )
    away_odds: Optional[Decimal] = Field(
        default=None, description="Can be None initially, filled in when odds arrive"
    )
    # Multiple spreads: spread_value -> {home_odds, away_odds}
    spread_lines: Dict[Decimal, Dict[str, Decimal]] = Field(
        default_factory=dict, description="Spread betting lines"
    )
    # Multiple totals: total_value -> {over_odds, under_odds}
    total_lines: Dict[Decimal, Dict[str, Decimal]] = Field(
        default_factory=dict, description="Total (over/under) betting lines"
    )
    last_odds_update: Optional[datetime] = None
    betting_closed_at: Optional[datetime] = None

    @computed_field
    def can_bet_pregame(self) -> bool:
        """True if PRE_GAME betting is allowed (status=SCHEDULED)."""
        return self.status == EventStatus.SCHEDULED

    @computed_field
    def can_bet_ingame(self) -> bool:
        """True if IN_GAME betting is allowed (status=LIVE)."""
        return self.status == EventStatus.LIVE


# =============================================================================
# Bet Request
# =============================================================================


@dataclass
class BetRequestMoneyline:
    """Moneyline bet request from agent to broker"""

    amount: Decimal
    selection: Literal["home", "away"]
    event_id: str
    order_type: OrderType
    betting_phase: BettingPhase
    limit_odds: Optional[Decimal] = None  # Required if order_type == LIMIT

    def validate(self) -> None:
        """Validate bet request parameters"""
        if self.amount <= 0:
            raise ValueError(f"Bet amount must be positive, got {self.amount}")

        if self.order_type == OrderType.LIMIT:
            if self.limit_odds is None:
                raise ValueError("limit_odds required for LIMIT orders")
            if self.limit_odds <= 1.0:
                raise ValueError(
                    f"limit_odds must be greater than 1.0, got {self.limit_odds}"
                )


@dataclass
class BetRequestSpread:
    """Spread bet request from agent to broker"""

    amount: Decimal
    selection: Literal["home", "away"]
    event_id: str
    order_type: OrderType
    betting_phase: BettingPhase
    spread_value: Decimal  # Required for SPREAD bets
    limit_odds: Optional[Decimal] = None  # Required if order_type == LIMIT

    def validate(self) -> None:
        """Validate bet request parameters"""
        if self.amount <= 0:
            raise ValueError(f"Bet amount must be positive, got {self.amount}")

        if self.order_type == OrderType.LIMIT:
            if self.limit_odds is None:
                raise ValueError("limit_odds required for LIMIT orders")
            if self.limit_odds <= 1.0:
                raise ValueError(
                    f"limit_odds must be greater than 1.0, got {self.limit_odds}"
                )


@dataclass
class BetRequestTotal:
    """Total (over/under) bet request from agent to broker"""

    amount: Decimal
    selection: Literal["over", "under"]
    event_id: str
    order_type: OrderType
    betting_phase: BettingPhase
    total_value: Decimal  # Required for TOTAL bets
    limit_odds: Optional[Decimal] = None  # Required if order_type == LIMIT

    def validate(self) -> None:
        """Validate bet request parameters"""
        if self.amount <= 0:
            raise ValueError(f"Bet amount must be positive, got {self.amount}")

        if self.order_type == OrderType.LIMIT:
            if self.limit_odds is None:
                raise ValueError("limit_odds required for LIMIT orders")
            if self.limit_odds <= 1.0:
                raise ValueError(
                    f"limit_odds must be greater than 1.0, got {self.limit_odds}"
                )


# Union type for all bet request types
BetRequest = Union[BetRequestMoneyline, BetRequestSpread, BetRequestTotal]


# =============================================================================
# Bet
# =============================================================================


class Bet(BaseModel):
    """Bet record"""

    bet_id: str
    agent_id: str
    event_id: str
    amount: Decimal
    selection: str
    odds: Decimal  # Actual execution odds
    order_type: OrderType
    limit_odds: Optional[Decimal] = None  # None for market orders
    betting_phase: BettingPhase
    create_time: datetime
    execution_time: Optional[datetime] = None  # None until executed
    status: BetStatus
    bet_type: BetType = Field(
        default=BetType.MONEYLINE,
        description="Type of bet (default: MONEYLINE for backward compatibility)",
    )
    spread_value: Optional[Decimal] = None  # For SPREAD bets
    total_value: Optional[Decimal] = None  # For TOTAL bets
    actual_payout: Optional[Decimal] = None
    outcome: Optional[BetOutcome] = None
    settlement_time: Optional[datetime] = None


@dataclass
class BetExecutedPayload:
    bet_id: str
    agent_id: str
    event_id: str
    selection: str
    amount: str
    execution_odds: str
    execution_time: str


@dataclass
class BetSettledPayload:
    bet_id: str
    agent_id: str
    event_id: str
    outcome: BetOutcome  # Store the enum directly
    payout: str
    winner: str


# =============================================================================
# Statistics
# =============================================================================


class Statistics(BaseModel):
    """Agent performance statistics"""

    total_bets: int
    total_wagered: Decimal
    wins: int
    losses: int
    win_rate: float
    net_profit: Decimal
    roi: float


__all__ = [
    # Enums
    "BetOutcome",
    "BetStatus",
    "BetType",
    "BettingPhase",
    "EventStatus",
    "OrderType",
    "VALID_STATUS_TRANSITIONS",
    # Models
    "Account",
    "Bet",
    "BetExecutedPayload",
    "BetRequest",
    "BetRequestMoneyline",
    "BetRequestSpread",
    "BetRequestTotal",
    "BetSettledPayload",
    "BettingEvent",
    "Statistics",
]
