"""Data models for the betting broker system.

All data classes and enums used throughout the betting system.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional


# =============================================================================
# Enums
# =============================================================================


class EventStatus(Enum):
    """Status of a betting event"""

    SCHEDULED = "SCHEDULED"  # Pre-game, accepting bets
    LIVE = "LIVE"  # In-game, accepting live bets only
    CLOSED = "CLOSED"  # Game ended, no more bets
    SETTLED = "SETTLED"  # All bets settled


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


# =============================================================================
# Account
# =============================================================================


@dataclass
class Account:
    """Agent account information"""

    agent_id: str
    balance: Decimal
    created_at: datetime
    last_updated: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "balance": str(self.balance),
            "created_at": self.created_at.isoformat(),
            "last_updated": self.last_updated.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Account":
        return cls(
            agent_id=data["agent_id"],
            balance=Decimal(data["balance"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_updated=datetime.fromisoformat(data["last_updated"]),
        )


# =============================================================================
# Event
# =============================================================================


@dataclass
class Event:
    """Betting event (e.g., a sports game)"""

    event_id: str
    home_team: str
    away_team: str
    game_time: datetime
    status: EventStatus
    home_odds: Decimal
    away_odds: Decimal
    last_odds_update: datetime
    betting_closed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "game_time": self.game_time.isoformat(),
            "status": self.status.value,
            "home_odds": str(self.home_odds),
            "away_odds": str(self.away_odds),
            "last_odds_update": self.last_odds_update.isoformat(),
            "betting_closed_at": (
                self.betting_closed_at.isoformat() if self.betting_closed_at else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        return cls(
            event_id=data["event_id"],
            home_team=data["home_team"],
            away_team=data["away_team"],
            game_time=datetime.fromisoformat(data["game_time"]),
            status=EventStatus(data["status"]),
            home_odds=Decimal(data["home_odds"]),
            away_odds=Decimal(data["away_odds"]),
            last_odds_update=datetime.fromisoformat(data["last_odds_update"]),
            betting_closed_at=(
                datetime.fromisoformat(data["betting_closed_at"])
                if data["betting_closed_at"]
                else None
            ),
        )


# =============================================================================
# Bet Request
# =============================================================================


@dataclass
class BetRequest:
    """Bet request from agent to broker"""

    amount: Decimal
    selection: str  # "home" or "away"
    event_id: str
    order_type: OrderType
    betting_phase: BettingPhase
    limit_odds: Optional[Decimal] = None  # Required if order_type == LIMIT

    def validate(self) -> None:
        """Validate bet request prameters"""
        if self.amount <= 0:
            raise ValueError(f"Bet amount must be positive, got {self.amount}")

        if self.selection not in ["home", "away"]:
            raise ValueError(
                f"Selection must be 'home' or 'away', got {self.selection}"
            )

        if self.order_type == OrderType.LIMIT:
            if self.limit_odds is None:
                raise ValueError("limit_odds required for LIMIT orders")
            if self.limit_odds <= 1.0:
                raise ValueError(
                    f"limit_odds must be greater than 1.0, got {self.limit_odds}"
                )


# =============================================================================
# Bet
# =============================================================================


@dataclass
class Bet:
    """Bet record"""

    bet_id: str
    agent_id: str
    event_id: str
    amount: Decimal
    selection: str
    odds: Decimal  # Actual execution odds
    order_type: OrderType
    limit_odds: Optional[Decimal]  # None for market orders
    betting_phase: BettingPhase
    create_time: datetime
    execution_time: Optional[datetime]  # None until executed
    status: BetStatus
    actual_payout: Optional[Decimal] = None
    outcome: Optional[BetOutcome] = None
    settlement_time: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bet_id": self.bet_id,
            "agent_id": self.agent_id,
            "event_id": self.event_id,
            "amount": str(self.amount),
            "selection": self.selection,
            "odds": str(self.odds),
            "order_type": self.order_type.value,
            "limit_odds": str(self.limit_odds) if self.limit_odds else None,
            "betting_phase": self.betting_phase.value,
            "create_time": self.create_time.isoformat(),
            "execution_time": (
                self.execution_time.isoformat() if self.execution_time else None
            ),
            "status": self.status.value,
            "actual_payout": str(self.actual_payout) if self.actual_payout else None,
            "outcome": self.outcome.value if self.outcome else None,
            "settlement_time": (
                self.settlement_time.isoformat() if self.settlement_time else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bet":
        return cls(
            bet_id=data["bet_id"],
            agent_id=data["agent_id"],
            event_id=data["event_id"],
            amount=Decimal(data["amount"]),
            selection=data["selection"],
            odds=Decimal(data["odds"]),
            order_type=OrderType(data["order_type"]),
            limit_odds=Decimal(data["limit_odds"]) if data["limit_odds"] else None,
            betting_phase=BettingPhase(data["betting_phase"]),
            create_time=datetime.fromisoformat(data["create_time"]),
            execution_time=(
                datetime.fromisoformat(data["execution_time"])
                if data["execution_time"]
                else None
            ),
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


# =============================================================================
# Statistics
# =============================================================================


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
        return {
            "total_bets": self.total_bets,
            "total_wagered": str(self.total_wagered),
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "net_profit": str(self.net_profit),
            "roi": self.roi,
        }
