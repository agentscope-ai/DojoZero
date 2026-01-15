"""NBA_moneyline Betting Broker

This module implements the betting broker operator that manages:
- Account balances
- Event lifecycle (pregame, odds updates, game start/end, settlement)
- Bet placement and execution (market and limit orders)
- Bet settlement
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import List, Sequence, Set, TypedDict

from dojozero.core import (
    ActorContext,
    Agent,
    Operator,
    OperatorBase,
    StreamEvent,
)

from dojozero.data._models import EventTypes

# Logger for broker operations
logger = logging.getLogger(__name__)

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
    home_odds: Optional[Decimal] = (
        None  # Can be None initially, filled in when odds arrive
    )
    away_odds: Optional[Decimal] = (
        None  # Can be None initially, filled in when odds arrive
    )
    last_odds_update: Optional[datetime] = None
    betting_closed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "game_time": self.game_time.isoformat(),
            "status": self.status.value,
            "home_odds": str(self.home_odds) if self.home_odds else None,
            "away_odds": str(self.away_odds) if self.away_odds else None,
            "last_odds_update": (
                self.last_odds_update.isoformat() if self.last_odds_update else None
            ),
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

    def __init__(self, config: BrokerOperatorConfig, trial_id: str):
        super().__init__(config["actor_id"], trial_id)

        # Account management
        self._accounts: Dict[str, Account] = {}
        self._agent_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Event management
        self._events: Dict[str, Event] = {}
        self._event_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Pending team info from GameUpdateEvent (waiting for OddsUpdateEvent)
        # Maps event_id -> {"home_team": str, "away_team": str, "game_time": datetime}
        self._pending_team_info: Dict[str, Dict[str, Any]] = {}

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
    def from_dict(
        cls, config: Dict[str, Any], context: ActorContext
    ) -> "BrokerOperator":
        """Create broker from configuration dictionary"""
        broker_config: BrokerOperatorConfig = {
            "actor_id": config["actor_id"],
            "initial_balance": config.get("initial_balance", "0"),
        }
        return cls(broker_config, context.trial_id)

    async def start(self) -> None:
        """Protocol hook: called before traffic is routed"""
        logger.info(
            "Operator '%s' starting (accounts=%d, events=%d, bets=%d)",
            self.actor_id,
            len(self._accounts),
            len(self._events),
            len(self._bets),
        )

    async def stop(self) -> None:
        """Protocol hook: called during shutdown"""
        logger.info(
            "Operator '%s' stopping - accounts=%d, events=%d, bets=%d",
            self.actor_id,
            len(self._accounts),
            len(self._events),
            len(self._bets),
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
        """Process incoming stream events and delegate to appropriate handlers.

        Expects StreamEvent.payload to be a DataEvent (OddsUpdateEvent, GameStartEvent, GameResultEvent, etc.)
        """
        try:
            data_event = event.payload

            # Type check - ensure it's one of our game events
            if not hasattr(data_event, "event_id"):
                logger.warning(
                    "Event missing event_id attribute: type=%s, payload=%s",
                    type(data_event),
                    data_event,
                )
                return

            event_id = data_event.event_id
            if not event_id:
                logger.error("Event missing event_id: %s", data_event)
                return

            async with self._event_locks[
                event_id
            ]:  # avoid multiple streamEvent change the same event
                # Use event_type property to determine event type (DataEvents have this)
                event_type = getattr(data_event, "event_type", None)
                if not event_type:
                    logger.warning(
                        "Event missing event_type property: type=%s, event_id=%s",
                        type(data_event),
                        event_id,
                    )
                    return

                # Log every incoming event
                logger.info(
                    "Received event: type=%s, event_id=%s, stream_id=%s, timestamp=%s",
                    event_type,
                    event_id,
                    event.stream_id,
                    getattr(data_event, "timestamp", None),
                )

                if event_type == EventTypes.GAME_INITIALIZE.value:
                    # GameInitializeEvent: Initialize event with team info (no odds yet)
                    home_team_str = getattr(data_event, "home_team", "")
                    away_team_str = getattr(data_event, "away_team", "")
                    game_time_dt = getattr(data_event, "game_time", None)

                    if not home_team_str or not away_team_str:
                        logger.warning(
                            "GameInitializeEvent missing team info: event_id=%s",
                            event_id,
                        )
                        return

                    if not isinstance(game_time_dt, datetime):
                        game_time_dt = datetime.now()

                    if event_id in self._events:
                        # Event already exists - update team info if needed
                        broker_event = self._events[event_id]
                        broker_event.home_team = home_team_str
                        broker_event.away_team = away_team_str
                        broker_event.game_time = game_time_dt
                        logger.info(
                            "Updated event from GameInitializeEvent: event_id=%s, home_team=%s, away_team=%s, game_time=%s",
                            event_id,
                            home_team_str,
                            away_team_str,
                            game_time_dt,
                        )
                    else:
                        # Initialize new event without odds (will be filled in when OddsUpdateEvent arrives)
                        logger.info(
                            "Initializing event from GameInitializeEvent: event_id=%s, home_team=%s, away_team=%s, game_time=%s (odds pending)",
                            event_id,
                            home_team_str,
                            away_team_str,
                            game_time_dt,
                        )
                        await self.initialize_event(
                            event_id=event_id,
                            home_team=home_team_str,
                            away_team=away_team_str,
                            game_time=game_time_dt,
                            initial_home_odds=None,  # Will be updated when OddsUpdateEvent arrives
                            initial_away_odds=None,  # Will be updated when OddsUpdateEvent arrives
                        )

                elif event_type == EventTypes.ODDS_UPDATE.value:
                    # Only process odds if we have team info (either from existing event or pending GameUpdateEvent)
                    if event_id in self._events:
                        # Event exists - update odds
                        logger.info(
                            "Updating odds: event_id=%s, home_odds=%s, away_odds=%s",
                            event_id,
                            getattr(data_event, "home_odds", None),
                            getattr(data_event, "away_odds", None),
                        )
                        await self.update_odds(
                            event_id=data_event.event_id,
                            home_odds=Decimal(str(data_event.home_odds)),
                            away_odds=Decimal(str(data_event.away_odds)),
                        )
                    elif event_id in self._pending_team_info:
                        # We have team info from GameUpdateEvent - initialize event with odds
                        team_info = self._pending_team_info[event_id]
                        logger.info(
                            "Initializing event from OddsUpdateEvent (team info already available): event_id=%s, home_team=%s, away_team=%s, home_odds=%s, away_odds=%s",
                            event_id,
                            team_info.get("home_team"),
                            team_info.get("away_team"),
                            getattr(data_event, "home_odds", None),
                            getattr(data_event, "away_odds", None),
                        )
                        await self.initialize_event(
                            event_id=event_id,
                            home_team=team_info["home_team"],
                            away_team=team_info["away_team"],
                            game_time=team_info["game_time"],
                            initial_home_odds=Decimal(str(data_event.home_odds)),
                            initial_away_odds=Decimal(str(data_event.away_odds)),
                        )
                        # Clear pending team info
                        del self._pending_team_info[event_id]
                    else:
                        # No team info yet - ignore this odds update
                        logger.debug(
                            "Ignoring OddsUpdateEvent (no team info available yet): event_id=%s, home_odds=%s, away_odds=%s",
                            event_id,
                            getattr(data_event, "home_odds", None),
                            getattr(data_event, "away_odds", None),
                        )

                elif event_type == EventTypes.GAME_UPDATE.value:
                    # Extract team names and game time from GameUpdateEvent
                    home_team_str = None
                    away_team_str = None
                    game_time_dt = None

                    if hasattr(data_event, "home_team") and isinstance(
                        data_event.home_team, dict
                    ):
                        home_city = data_event.home_team.get("teamCity", "")
                        home_name = data_event.home_team.get("teamName", "")
                        if home_city or home_name:
                            home_team_str = f"{home_city} {home_name}".strip()

                    if hasattr(data_event, "away_team") and isinstance(
                        data_event.away_team, dict
                    ):
                        away_city = data_event.away_team.get("teamCity", "")
                        away_name = data_event.away_team.get("teamName", "")
                        if away_city or away_name:
                            away_team_str = f"{away_city} {away_name}".strip()

                    # Extract game_time_utc if available
                    if (
                        hasattr(data_event, "game_time_utc")
                        and data_event.game_time_utc
                    ):
                        try:
                            game_time_dt = datetime.fromisoformat(
                                data_event.game_time_utc.replace("Z", "+00:00")
                            )
                        except (ValueError, AttributeError):
                            pass

                    # Only process if we have both team names
                    if not home_team_str or not away_team_str:
                        logger.debug(
                            "GameUpdateEvent missing team info: event_id=%s, home_team=%s, away_team=%s",
                            event_id,
                            home_team_str,
                            away_team_str,
                        )
                        return

                    # Use current time as fallback if game_time_utc not available
                    if not game_time_dt:
                        game_time_dt = datetime.now()

                    if event_id in self._events:
                        # Event exists - update team names and game_time
                        broker_event = self._events[event_id]
                        broker_event.home_team = home_team_str
                        broker_event.away_team = away_team_str
                        broker_event.game_time = game_time_dt
                        logger.info(
                            "Updated event from GameUpdateEvent: event_id=%s, home_team=%s, away_team=%s, game_time=%s",
                            event_id,
                            home_team_str,
                            away_team_str,
                            game_time_dt,
                        )
                    else:
                        # Event doesn't exist yet - store team info for when OddsUpdateEvent arrives
                        self._pending_team_info[event_id] = {
                            "home_team": home_team_str,
                            "away_team": away_team_str,
                            "game_time": game_time_dt,
                        }
                        logger.info(
                            "Stored team info from GameUpdateEvent (waiting for OddsUpdateEvent): event_id=%s, home_team=%s, away_team=%s, game_time=%s",
                            event_id,
                            home_team_str,
                            away_team_str,
                            game_time_dt,
                        )

                elif event_type == EventTypes.GAME_START.value:
                    await self.update_event_status(
                        event_id=data_event.event_id, status=EventStatus.LIVE
                    )

                elif event_type == EventTypes.GAME_RESULT.value:
                    await self.update_event_status(
                        event_id=data_event.event_id, status=EventStatus.CLOSED
                    )
                    await self.settle_event(
                        event_id=data_event.event_id,
                        winner=data_event.winner,
                        final_score=data_event.final_score,
                    )
                else:
                    logger.warning("Unknown event type: %s", event_type)

        except Exception as e:
            logger.error("Failed to handle stream event: %s", e, exc_info=True)

    async def initialize_event(
        self,
        event_id: str,
        home_team: str,
        away_team: str,
        game_time: datetime,
        initial_home_odds: Optional[Decimal] = None,
        initial_away_odds: Optional[Decimal] = None,
    ) -> Event:
        """Initialize a new betting event.

        Args:
            event_id: Unique event identifier
            home_team: Home team name
            away_team: Away team name
            game_time: Scheduled game time
            initial_home_odds: Optional initial home odds (can be None if not yet available)
            initial_away_odds: Optional initial away odds (can be None if not yet available)
        """

        if event_id in self._events:
            raise ValueError(f"Event {event_id} already exists")

        # Validate odds if provided
        if initial_home_odds is not None and initial_home_odds <= 1.0:
            raise ValueError("Odds must be greater than 1.0")
        if initial_away_odds is not None and initial_away_odds <= 1.0:
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
            last_odds_update=now if (initial_home_odds and initial_away_odds) else None,
        )

        self._events[event_id] = event
        if initial_home_odds and initial_away_odds:
            logger.info(
                "Created event %s: %s vs %s (Odds: %s/%s)",
                event_id,
                home_team,
                away_team,
                initial_home_odds,
                initial_away_odds,
            )
        else:
            logger.info(
                "Created event %s: %s vs %s (Odds: pending)",
                event_id,
                home_team,
                away_team,
            )
        return event

    async def update_odds(
        self, event_id: str, home_odds: Decimal, away_odds: Decimal
    ) -> Event:
        """Update odds for an event and execute matching limit orders.

        Can be called to set initial odds (if event was initialized without odds)
        or to update existing odds.
        """
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

        logger.info(
            "Updated odds for event %s: home=%s, away=%s (status=%s)",
            event_id,
            home_odds,
            away_odds,
            event.status.value,
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

        logger.info(
            "Event %s status changed: %s → %s",
            event_id,
            event.status.value,
            status.value,
        )

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
                f"Cannot settle event with status {event.status.value}, must be CLOSED"
            )

        if winner not in ["home", "away"]:
            raise ValueError(f"Invalid winner: {winner}")

        logger.info(
            "Settling event %s - Winner: %s, Score: %s",
            event_id,
            winner,
            final_score,
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

        logger.info(
            "Completed settlement for event %s - Settled %d bets",
            event_id,
            settled_count,
        )

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
            logger.info(
                "Bet %s settled - Agent %s: %s, Payout: %s",
                bet.bet_id,
                bet.agent_id,
                outcome,
                payout,
            )

            # Notify agent
            notification = StreamEvent(
                stream_id=f"settlement_{bet.bet_id}",
                payload=BetSettledPayload(
                    bet_id=bet.bet_id,
                    agent_id=bet.agent_id,
                    event_id=bet.event_id,
                    outcome=outcome,
                    payout=str(payout),
                    winner=winner,
                ),
                emitted_at=datetime.now(),
            )
            asyncio.create_task(self._notify_agent(bet.agent_id, notification))

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
            logger.info(
                "Cancelled %d pre-game orders for event %s", cancelled_count, event_id
            )

    async def _cancel_all_pending_orders(self, event_id: str) -> None:
        """Cancel all pending orders for an event"""
        pending_bet_ids = list(self._event_pending_orders.get(event_id, set()))

        for bet_id in pending_bet_ids:
            bet = self._bets[bet_id]
            await self._cancel_pending_order(bet)

        if pending_bet_ids:
            logger.info(
                "Cancelled %d pending orders for event %s",
                len(pending_bet_ids),
                event_id,
            )

    async def _cancel_pending_order(self, bet: Bet) -> None:
        """Cancel a pending order and refund"""
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

        logger.info(
            "Bet %s cancelled - Refunded %s to %s",
            bet.bet_id,
            bet.amount,
            bet.agent_id,
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

        logger.info("Created account for %s with balance %s", agent_id, initial_balance)
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

            logger.info(
                "Deposit for %s: +%s (balance: %s)", agent_id, amount, account.balance
            )
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

            logger.info(
                "Withdraw for %s: -%s (balance: %s)", agent_id, amount, account.balance
            )
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

                # Check odds are available (event must be initialized with odds)
                if event.home_odds is None or event.away_odds is None:
                    raise ValueError(
                        f"Odds not yet available for event {bet_request.event_id}. "
                        "Please wait for odds to be updated."
                    )

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
                # Odds should already be validated above, but add safety check
                if bet_request.selection == "home":
                    execution_odds = event.home_odds
                else:
                    execution_odds = event.away_odds

                if execution_odds is None:
                    raise ValueError(
                        f"Odds not available for selection '{bet_request.selection}' "
                        f"on event {bet_request.event_id}"
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
                    logger.info(
                        "Limit order placed - %s: %s $%s on %s @ %s+",
                        bet_id,
                        agent_id,
                        bet_request.amount,
                        bet_request.selection,
                        bet_request.limit_odds,
                    )

                return "bet_placed"

        except (ValueError, Exception) as e:
            logger.error("Bet rejected for %s: %s", agent_id, e, exc_info=True)
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

        logger.info(
            "Bet %s executed - %s $%s on %s @ %s",
            bet.bet_id,
            bet.agent_id,
            bet.amount,
            bet.selection,
            execution_odds,
        )

        # Send execution notification to agent
        notification = StreamEvent(
            stream_id=f"execution_{bet.bet_id}",
            payload=BetExecutedPayload(
                bet_id=bet.bet_id,
                agent_id=bet.agent_id,
                event_id=bet.event_id,
                selection=bet.selection,
                amount=str(bet.amount),
                execution_odds=str(execution_odds),
                execution_time=bet.execution_time.isoformat(),
            ),
            emitted_at=datetime.now(),
        )
        asyncio.create_task(self._notify_agent(bet.agent_id, notification))

    async def cancel_bet(self, agent_id: str, bet_id: str) -> str:
        """Cancel a pending limit order and refund locked funds.

        Returns:
            "bet_cancelled" - Bet successfully cancelled and funds refunded
            "cancel_failed" - Cancellation failed (see logs for reason)
        """
        if bet_id not in self._bets:
            logger.warning("Bet %s not found", bet_id)
            return "cancel_failed"

        bet = self._bets[bet_id]

        if bet.agent_id != agent_id:
            logger.warning("Bet %s does not belong to agent %s", bet_id, agent_id)
            return "cancel_failed"

        if bet.status != BetStatus.PENDING:
            logger.warning("Bet %s is %s, cannot cancel", bet_id, bet.status.value)
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
        total_payout = Decimal(0)  # Changed: track total payouts

        for bet_id in all_bet_ids:
            bet = self._bets[bet_id]
            total_wagered += bet.amount

            if bet.status == BetStatus.SETTLED:
                if bet.outcome == BetOutcome.WIN:
                    wins += 1
                    total_payout += bet.actual_payout or Decimal(0)  # Add payout
                elif bet.outcome == BetOutcome.LOSS:
                    losses += 1
                    # Don't add to total_payout (it's 0 for losses)

        # Calculate metrics
        settled_bets = wins + losses
        win_rate = wins / settled_bets if settled_bets > 0 else 0.0
        net_profit = total_payout - total_wagered  # Fixed: payout - wagered
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

    async def save_state(self) -> Dict[str, Any]:
        """Export operator state for persistence"""
        # Get all event and agent IDs
        event_ids = list(self._events.keys())
        agent_ids = list(self._accounts.keys())

        # Acquire all locks in a consistent order to prevent deadlocks
        all_locks = [
            (f"event_{eid}", self._event_locks[eid]) for eid in sorted(event_ids)
        ]
        all_locks += [
            (f"agent_{aid}", self._agent_locks[aid]) for aid in sorted(agent_ids)
        ]

        # Acquire locks sequentially
        for _, lock in all_locks:
            await lock.acquire()

        try:
            return {
                "actor_id": self.actor_id,
                "accounts": {
                    agent_id: account.to_dict()
                    for agent_id, account in self._accounts.items()
                },
                "events": {
                    event_id: event.to_dict()
                    for event_id, event in self._events.items()
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
        finally:
            # Release all locks in reverse order
            for _, lock in reversed(all_locks):
                lock.release()

    async def load_state(self, state: Dict[str, Any]) -> None:
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
        from dojozero.agents._toolkit import tool  # type: ignore[import-untyped]

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
