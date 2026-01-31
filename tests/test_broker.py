"""
Unit tests for Sports Betting Broker Operator using pytest
"""

import pytest
import pytest_asyncio  # pyright: ignore
from decimal import Decimal
from datetime import datetime
from typing import Any

from dojozero.betting import (
    BrokerOperator,
    BrokerOperatorConfig,
    BetRequestMoneyline,
    BetRequestSpread,
    BetRequestTotal,
    OrderType,
    BetStatus,
    BetOutcome,
    BetExecutedPayload,
    BetSettledPayload,
    BetType,
)

from dojozero.core import StreamEvent
from dojozero.data._models import (
    GameInitializeEvent,
    GameStartEvent,
    GameResultEvent,
    OddsUpdateEvent,
)
from dojozero.data._models import MoneylineOdds, SpreadOdds, TotalOdds, OddsInfo

pytestmark = pytest.mark.asyncio


# =============================================================================
# Fixtures
# =============================================================================


class MockAgent:
    """Mock Agent implementation for testing"""

    def __init__(self, actor_id: str):
        self.actor_id = actor_id
        self.received_events = []

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process asynchronous notifications from the broker."""
        self.received_events.append(event)
        payload = event.payload

        if isinstance(payload, BetExecutedPayload):
            print(
                f"[AGENT {self.actor_id}] Bet {payload.bet_id} executed "
                f"at probability {payload.execution_probability} (shares: {payload.shares})"
            )
        elif isinstance(payload, BetSettledPayload):
            print(
                f"[AGENT {self.actor_id}] Bet {payload.bet_id} settled: "
                f"{payload.outcome}, Payout: ${payload.payout}"
            )


@pytest_asyncio.fixture  # Use pytest_asyncio.fixture for async fixtures
async def broker():
    """Create and start a broker for testing"""
    config: BrokerOperatorConfig = {
        "actor_id": "test_broker",
        "initial_balance": "1000.00",
    }
    broker = BrokerOperator(config, trial_id="test-trial")
    await broker.start()
    yield broker
    await broker.stop()


@pytest.fixture  # Regular fixture (not async)
def agent():
    """Create a mock agent"""
    return MockAgent(actor_id="test_agent")


@pytest_asyncio.fixture
async def broker_with_agent(broker, agent):
    """Broker with registered agent"""
    await broker.register_agents([agent])  # type: ignore[arg-type]
    return broker, agent


@pytest_asyncio.fixture
async def initialized_event(broker):
    """Create broker with initialized event"""
    game_init_event = StreamEvent(
        stream_id="nba_game_stream",
        payload=GameInitializeEvent(
            game_id="lakers_vs_warriors",
            home_team="Lakers",
            away_team="Warriors",
            game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
        ),
        emitted_at=datetime.now(),
    )
    await broker.handle_stream_event(game_init_event)

    # Add odds update to set initial probabilities
    odds_event = StreamEvent(
        stream_id="nba_odds_stream",
        payload=OddsUpdateEvent(
            game_id="lakers_vs_warriors",
            odds=OddsInfo(
                moneyline=MoneylineOdds(
                    home_probability=0.513,  # 1/1.95
                    away_probability=0.476,  # 1/2.10
                    home_odds=1.95,
                    away_odds=2.10,
                )
            ),
        ),
        emitted_at=datetime.now(),
    )
    await broker.handle_stream_event(odds_event)
    return broker, "lakers_vs_warriors"


# =============================================================================
# Account Management Tests
# =============================================================================


class TestAccountManagement:
    """Test account creation and balance operations"""

    async def test_create_account(self, broker):
        """Test creating a new account"""
        account = await broker.create_account("agent1", Decimal("500.00"))

        assert account.agent_id == "agent1"
        assert account.balance == Decimal("500.00")
        assert account.created_at is not None

    async def test_create_duplicate_account_raises_error(self, broker):
        """Test that creating duplicate account raises error"""
        await broker.create_account("agent1", Decimal("500.00"))

        with pytest.raises(ValueError, match="already exists"):
            await broker.create_account("agent1", Decimal("500.00"))

    async def test_create_account_negative_balance_raises_error(self, broker):
        """Test that negative initial balance raises error"""
        with pytest.raises(ValueError, match="non-negative"):
            await broker.create_account("agent1", Decimal("-100.00"))

    async def test_get_balance(self, broker_with_agent):
        """Test retrieving account balance"""
        broker, agent = broker_with_agent

        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("1000.00")

    async def test_get_balance_nonexistent_account_raises_error(self, broker):
        """Test that getting balance for nonexistent account raises error"""
        with pytest.raises(ValueError, match="Account not found"):
            await broker.get_balance("nonexistent_agent")

    async def test_deposit(self, broker_with_agent):
        """Test depositing funds"""
        broker, agent = broker_with_agent

        new_balance = await broker.deposit(agent.actor_id, Decimal("500.00"))

        assert new_balance == Decimal("1500.00")
        assert await broker.get_balance(agent.actor_id) == Decimal("1500.00")

    async def test_deposit_negative_amount_raises_error(self, broker_with_agent):
        """Test that depositing negative amount raises error"""
        broker, agent = broker_with_agent

        with pytest.raises(ValueError, match="must be positive"):
            await broker.deposit(agent.actor_id, Decimal("-100.00"))

    async def test_withdraw(self, broker_with_agent):
        """Test withdrawing funds"""
        broker, agent = broker_with_agent

        new_balance = await broker.withdraw(agent.actor_id, Decimal("300.00"))

        assert new_balance == Decimal("700.00")
        assert await broker.get_balance(agent.actor_id) == Decimal("700.00")

    async def test_withdraw_insufficient_balance_raises_error(self, broker_with_agent):
        """Test that withdrawing more than balance raises error"""
        broker, agent = broker_with_agent

        with pytest.raises(ValueError, match="Insufficient balance"):
            await broker.withdraw(agent.actor_id, Decimal("2000.00"))


# =============================================================================
# Event Management Tests
# =============================================================================


class TestEventManagement:
    """Test event lifecycle management"""

    async def test_initialize_event(self, broker):
        """Test creating a new betting event via events"""
        # Initialize via GameInitializeEvent
        game_init = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="game1",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.now(),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init)

        # Set odds via OddsUpdateEvent
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="game1",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        event = await broker.get_available_event()
        assert event is not None
        quote = event.model_dump(mode="json")
        assert quote["event_id"] == "game1"
        assert quote["home_team"] == "Lakers"
        assert quote["away_team"] == "Warriors"
        assert quote["status"] == "SCHEDULED"
        assert Decimal(quote["home_probability"]) == Decimal("0.513")
        assert Decimal(quote["away_probability"]) == Decimal("0.476")

    async def test_initialize_duplicate_event_raises_error(self, broker):
        """Test that creating duplicate event raises error"""
        await broker._initialize_event(
            event_id="game1",
            home_team="Lakers",
            away_team="Warriors",
            game_time=datetime.now(),
        )

        with pytest.raises(ValueError, match="already exists"):
            await broker._initialize_event(
                event_id="game1",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.now(),
            )

    async def test_initialize_event_invalid_probability_raises_error(self, broker):
        """Test that probability outside 0-1 range raises error when updating probabilities"""
        # Initialize event first
        await broker._initialize_event(
            event_id="game1",
            home_team="Lakers",
            away_team="Warriors",
            game_time=datetime.now(),
        )
        # Probability validation happens in _update_probabilities, not _initialize_event
        with pytest.raises(ValueError, match="between 0 and 1"):
            await broker._update_probabilities(
                event_id="game1",
                home_probability=Decimal("1.5"),  # Invalid: > 1
                away_probability=Decimal("0.476"),
            )

    async def test_get_available_event_with_initialized(self, initialized_event):
        """Test getting the current event from initialized event"""
        broker, event_id = initialized_event

        event = await broker.get_available_event()
        assert event is not None
        quote = event.model_dump(mode="json")

        assert quote["event_id"] == event_id
        assert quote["home_team"] == "Lakers"
        assert quote["away_team"] == "Warriors"
        assert quote["status"] == "SCHEDULED"
        assert Decimal(quote["home_probability"]) == Decimal("0.513")
        assert Decimal(quote["away_probability"]) == Decimal("0.476")

    async def test_get_available_event_nonexistent_returns_none(self, broker):
        """Test that getting event when none exists returns None"""
        event = await broker.get_available_event()
        assert event is None

    async def test_update_probabilities(self, initialized_event):
        """Test updating event odds via OddsUpdateEvent"""
        broker, event_id = initialized_event

        # Update probabilities via event
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id=event_id,
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.50,  # 1/2.00
                        away_probability=0.455,  # 1/2.20
                        home_odds=2.00,
                        away_odds=2.20,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        event = await broker.get_available_event()
        assert event is not None
        quote = event.model_dump(mode="json")
        assert Decimal(quote["home_probability"]) == Decimal("0.50")
        assert Decimal(quote["away_probability"]) == Decimal("0.455")

    async def test_update_probabilities_invalid_probability_raises_error(
        self, initialized_event
    ):
        """Test that invalid probability update raises error"""
        broker, event_id = initialized_event

        with pytest.raises(ValueError, match="between 0 and 1"):
            await broker._update_probabilities(
                event_id=event_id,
                home_probability=Decimal("1.5"),  # Invalid: > 1
                away_probability=Decimal("0.455"),
            )

    async def test_update_event_status_to_live(self, initialized_event):
        """Test transitioning event to LIVE status via GameStartEvent"""
        broker, event_id = initialized_event

        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id=event_id),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        event = await broker.get_available_event()
        assert event is not None
        quote = event.model_dump(mode="json")
        assert quote["status"] == "LIVE"

    async def test_update_event_status_to_closed(self, initialized_event):
        """Test transitioning event to CLOSED status via GameResultEvent"""
        broker, event_id = initialized_event

        # First transition to LIVE
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id=event_id),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        # Then transition to CLOSED via game result
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id=event_id,
                winner="home",
                home_score=110,
                away_score=105,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        event = await broker.get_available_event()
        # After settlement, event is no longer available (status is SETTLED)
        assert event is None

    async def test_get_available_event(self, broker):
        """Test getting the current event accepting bets"""
        # Create event
        game_init = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="game1",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.now(),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init)

        odds = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="game1",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds)

        # Verify event is available (SCHEDULED status)
        available = await broker.get_available_event()
        assert available is not None
        assert available.event_id == "game1"
        assert available.status.value == "SCHEDULED"

        # Close event via game start + result
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id="game1"),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        # Event should still be available (LIVE status)
        available = await broker.get_available_event()
        assert available is not None
        assert available.status.value == "LIVE"

        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id="game1",
                winner="home",
                home_score=100,
                away_score=95,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # After settlement, event is no longer available
        available = await broker.get_available_event()
        assert available is None


# =============================================================================
# Bet Placement Tests
# =============================================================================


class TestBetPlacement:
    """Test bet placement and execution"""

    async def test_place_market_bet(self, broker_with_agent):
        """Test placing a market order (immediate execution)"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        result = await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
            ),
        )

        assert result == "bet_placed"

        # Check balance was deducted
        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("900.00")

        # Check bet was executed
        active_bets = await broker.get_active_bets(agent.actor_id)
        assert len(active_bets) == 1
        assert active_bets[0].amount == Decimal("100.00")
        assert active_bets[0].status == BetStatus.ACTIVE

    async def test_place_limit_order(self, broker_with_agent):
        """Test placing a limit order (conditional execution)"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        result = await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("50.00"),
                selection="away",
                event_id="test_event",
                order_type=OrderType.LIMIT,
                limit_probability=Decimal("0.455"),  # 1/2.20
            ),
        )

        assert result == "bet_placed"

        # Check balance was deducted
        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("950.00")

        # Check order is pending (not executed yet)
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 1
        assert pending[0].amount == Decimal("50.00")
        assert pending[0].status == BetStatus.PENDING

    async def test_limit_order_execution_on_odds_update(self, broker_with_agent):
        """Test that limit order executes when odds reach threshold"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place limit order
        await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("50.00"),
                selection="away",
                event_id="test_event",
                order_type=OrderType.LIMIT,
                limit_probability=Decimal("0.455"),  # 1/2.20
            ),
        )

        # Update probabilities to trigger execution
        odds_update = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.526,  # 1/1.90
                        away_probability=0.455,  # 1/2.20 (>= 0.455 limit, should execute)
                        home_odds=1.90,
                        away_odds=2.20,  # 1/2.20 = 0.455
                    )
                ),  # away_probability >= limit_probability, should trigger
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_update)

        # Check order executed
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 0

        active = await broker.get_active_bets(agent.actor_id)
        assert len(active) == 1
        assert active[0].status == BetStatus.ACTIVE
        assert abs(active[0].probability - Decimal("0.455")) < Decimal(
            "0.001"
        )  # 1/2.20

    async def test_place_bet_insufficient_balance(self, broker_with_agent):
        """Test that bet is rejected with insufficient balance"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        result = await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("10000.00"),  # More than balance
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
            ),
        )

        assert result == "bet_invalid"

        # Balance should be unchanged
        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("1000.00")

    async def test_place_bet_on_closed_event(self, broker_with_agent):
        """Test that bet is rejected on closed event"""
        broker, agent = broker_with_agent

        # Initialize and close event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Close event via game start + result
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id="test_event"),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id="test_event",
                winner="home",
                home_score=110,
                away_score=105,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        result = await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
            ),
        )

        assert result == "bet_invalid"

    async def test_cancel_pending_bet(self, broker_with_agent):
        """Test cancelling a pending limit order"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place limit order
        await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("50.00"),
                selection="away",
                event_id="test_event",
                order_type=OrderType.LIMIT,
                limit_probability=Decimal("0.455"),  # 1/2.20
            ),
        )

        pending = await broker.get_pending_orders(agent.actor_id)
        bet_id = pending[0].bet_id

        # Cancel the bet
        result = await broker.cancel_bet(agent.actor_id, bet_id)

        assert result == "bet_cancelled"

        # Check funds refunded
        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("1000.00")

        # Check no pending orders
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 0

    async def test_cancel_active_bet_fails(self, broker_with_agent):
        """Test that cancelling an active bet fails"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place and execute market bet
        await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
            ),
        )

        active = await broker.get_active_bets(agent.actor_id)
        bet_id = active[0].bet_id

        # Try to cancel active bet
        result = await broker.cancel_bet(agent.actor_id, bet_id)

        assert result == "cancel_failed"


# =============================================================================
# Settlement Tests
# =============================================================================


class TestBetSettlement:
    """Test bet settlement and payout calculation"""

    async def test_settle_winning_bet(self, broker_with_agent):
        """Test settling a winning bet"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place bet on home team
        await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
            ),
        )

        # Start game
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id="test_event"),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        # Game result (this also closes the event)
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id="test_event",
                winner="home",
                home_score=110,
                away_score=105,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Check bet settled with payout (Polymarket model: shares * $1.00)
        history = await broker.get_bet_history(agent.actor_id)
        assert len(history) == 1
        assert history[0].outcome == BetOutcome.WIN
        # Shares = amount / probability = 100 / 0.513 ≈ 194.93
        # Payout = shares * 1.00 = 194.93
        expected_shares = Decimal("100.00") / Decimal("0.513")
        assert history[0].actual_payout == expected_shares * Decimal("1.00")

        # Check balance updated
        balance = await broker.get_balance(agent.actor_id)
        expected_balance = Decimal("900.00") + (expected_shares * Decimal("1.00"))
        assert balance == expected_balance

    async def test_settle_losing_bet(self, broker_with_agent):
        """Test settling a losing bet"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place bet on home team
        await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
            ),
        )

        # Start game
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id="test_event"),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        # Game result (this also closes the event)
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id="test_event",
                winner="away",
                home_score=105,
                away_score=110,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Check bet settled as loss
        history = await broker.get_bet_history(agent.actor_id)
        assert len(history) == 1
        assert history[0].outcome == BetOutcome.LOSS
        assert history[0].actual_payout == Decimal("0")

        # Check balance (still at 900 after bet, no payout)
        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("900.00")

    async def test_pending_orders_remain_active_on_game_start(self, broker_with_agent):
        """Test that pending limit orders remain active when game starts (not cancelled)"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place limit order
        await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("50.00"),
                selection="away",
                event_id="test_event",
                order_type=OrderType.LIMIT,
                limit_probability=Decimal("0.455"),  # 1/2.20
            ),
        )

        # Verify order is pending before game starts
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 1
        balance_before = await broker.get_balance(agent.actor_id)
        assert balance_before == Decimal("950.00")  # Funds locked

        # Start game
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id="test_event"),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        # Check order is still pending (not cancelled) - betting continues during live gameplay
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 1

        balance_after = await broker.get_balance(agent.actor_id)
        assert balance_after == Decimal("950.00")  # Funds still locked

        # Now close the game - orders should be cancelled
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id="test_event",
                winner="home",
                home_score=110,
                away_score=105,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Check order was cancelled when game closed
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 0

        balance_final = await broker.get_balance(agent.actor_id)
        assert balance_final == Decimal("1000.00")  # Funds refunded


# =============================================================================
# Statistics Tests
# =============================================================================


class TestStatistics:
    """Test performance statistics calculation"""

    async def test_statistics_with_wins_and_losses(self, broker_with_agent):
        """Test statistics calculation with multiple bets"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place two bets
        await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
            ),
        )

        await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("50.00"),
                selection="away",
                event_id="test_event",
                order_type=OrderType.MARKET,
            ),
        )

        # Start game and settle with home team winning
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id="test_event"),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id="test_event",
                winner="home",
                home_score=110,
                away_score=105,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Get statistics
        stats = await broker.get_statistics(agent.actor_id)

        assert stats.total_bets == 2
        assert stats.total_wagered == Decimal("150.00")
        assert stats.wins == 1
        assert stats.losses == 1
        assert stats.win_rate == 0.5

        # Net profit = win_payout - total_wagered (Polymarket model)
        # Home bet: shares = 100 / 0.513 ≈ 194.93, payout = 194.93
        home_shares = Decimal("100.00") / Decimal("0.513")
        expected_profit = (home_shares * Decimal("1.00")) - Decimal("150.00")
        assert abs(stats.net_profit - expected_profit) < Decimal(
            "0.01"
        )  # Allow small rounding

    async def test_statistics_no_bets(self, broker_with_agent):
        """Test statistics for agent with no bets"""
        broker, agent = broker_with_agent

        stats = await broker.get_statistics(agent.actor_id)

        assert stats.total_bets == 0
        assert stats.total_wagered == Decimal("0")
        assert stats.wins == 0
        assert stats.losses == 0
        assert stats.win_rate == 0.0
        assert stats.net_profit == Decimal("0")
        assert stats.roi == 0.0


# =============================================================================
# State Management Tests
# =============================================================================


class TestStateManagement:
    """Test state save/load functionality"""

    async def test_save_and_load_state(self, broker_with_agent):
        """Test saving and loading broker state"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place a bet
        await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
            ),
        )

        # Save state
        state = await broker.save_state()

        # Create new broker and load state
        new_broker = BrokerOperator({"actor_id": "new_broker"}, trial_id="test-trial")
        await new_broker.load_state(state)

        # Verify state was restored
        balance = await new_broker.get_balance(agent.actor_id)
        assert balance == Decimal("900.00")

        active_bets = await new_broker.get_active_bets(agent.actor_id)
        assert len(active_bets) == 1

        event = await new_broker.get_available_event()
        assert event is not None
        quote = event.model_dump(mode="json")
        assert quote["event_id"] == "test_event"


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """End-to-end integration tests"""

    async def test_complete_betting_workflow(self, broker_with_agent):
        """Test complete workflow: event creation → betting → settlement"""
        broker, agent = broker_with_agent

        # 1. Initialize event
        game_init = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="game1",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init)

        # Set initial odds
        odds_init = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="game1",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_init)

        # 2. Place market bet
        result = await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("100.00"),
                selection="home",
                event_id="game1",
                order_type=OrderType.MARKET,
            ),
        )
        assert result == "bet_placed"

        # 3. Place limit order
        result = await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("50.00"),
                selection="away",
                event_id="game1",
                order_type=OrderType.LIMIT,
                limit_probability=Decimal("0.455"),  # 1/2.20
            ),
        )
        assert result == "bet_placed"

        # 4. Update probabilities (trigger limit order)
        odds_update = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="game1",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.526,
                        away_probability=0.455,  # 1/2.20 >= 0.455 limit (should execute)
                        home_odds=1.90,
                        away_odds=2.20,  # 1/2.20 = 0.455
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_update)

        # 5. Verify both bets active
        active = await broker.get_active_bets(agent.actor_id)
        assert len(active) == 2

        # 6. Start game
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id="game1"),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        # 7. End and settle game
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id="game1",
                winner="home",
                home_score=110,
                away_score=105,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # 8. Verify settlement
        history = await broker.get_bet_history(agent.actor_id)
        assert len(history) == 2

        # Home bet won
        home_bet = [b for b in history if b.selection == "home"][0]
        assert home_bet.outcome == BetOutcome.WIN

        # Away bet lost
        away_bet = [b for b in history if b.selection == "away"][0]
        assert away_bet.outcome == BetOutcome.LOSS

        # 9. Check final balance (Polymarket model: shares * $1.00 for win)
        balance = await broker.get_balance(agent.actor_id)
        home_shares = Decimal("100.00") / Decimal("0.513")
        expected = Decimal("850.00") + (home_shares * Decimal("1.00"))
        assert abs(balance - expected) < Decimal("0.01")  # Allow small rounding

        # 10. Check statistics
        stats = await broker.get_statistics(agent.actor_id)
        assert stats.total_bets == 2
        assert stats.wins == 1


# =============================================================================
# Spread and Total Betting Tests
# =============================================================================


def create_odds_event_with_spreads_totals(
    game_id: str,
    home_odds: float = 1.95,
    away_odds: float = 2.10,
    spread_updates: list | None = None,
    total_updates: list | None = None,
):
    """Helper to create an OddsUpdateEvent with spread/total updates for testing."""
    moneyline = MoneylineOdds(
        home_odds=home_odds,
        away_odds=away_odds,
        home_probability=1.0 / home_odds if home_odds > 0 else 0.0,
        away_probability=1.0 / away_odds if away_odds > 0 else 0.0,
    )

    spreads: list[SpreadOdds] = []
    if spread_updates:
        for su in spread_updates:
            h_odds = su.get("home_odds", 1.0)
            a_odds = su.get("away_odds", 1.0)
            h_prob = 1.0 / h_odds if h_odds > 0 else 0.0
            a_prob = 1.0 / a_odds if a_odds > 0 else 0.0
            spreads.append(
                SpreadOdds(
                    spread=su["spread"],
                    home_probability=h_prob,
                    away_probability=a_prob,
                    home_odds=h_odds,
                    away_odds=a_odds,
                )
            )

    totals: list[TotalOdds] = []
    if total_updates:
        for tu in total_updates:
            over_odds = tu.get("over_odds", 1.0)
            under_odds = tu.get("under_odds", 1.0)
            over_prob = 1.0 / over_odds if over_odds > 0 else 0.0
            under_prob = 1.0 / under_odds if under_odds > 0 else 0.0
            totals.append(
                TotalOdds(
                    total=tu["total"],
                    over_probability=over_prob,
                    under_probability=under_prob,
                    over_odds=over_odds,
                    under_odds=under_odds,
                )
            )

    return OddsUpdateEvent(
        game_id=game_id,
        odds=OddsInfo(
            provider="test",
            moneyline=moneyline,
            spreads=spreads,
            totals=totals,
        ),
    )


class TestSpreadBetting:
    """Test spread betting functionality"""

    async def test_place_spread_bet(self, broker_with_agent):
        """Test placing a spread bet"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set odds with spread updates
        odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            spread_updates=[
                {"spread": -3.5, "home_odds": 1.90, "away_odds": 1.90},
                {"spread": -4.5, "home_odds": 1.85, "away_odds": 1.95},
            ],
        )
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Place spread bet
        result = await broker.place_bet(
            agent.actor_id,
            BetRequestSpread(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                spread_value=Decimal("-3.5"),
            ),
        )

        assert result == "bet_placed"

        # Check balance was deducted
        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("900.00")

        # Check bet was executed
        active_bets = await broker.get_active_bets(agent.actor_id)
        assert len(active_bets) == 1
        assert active_bets[0].bet_type == BetType.SPREAD
        assert active_bets[0].spread_value == Decimal("-3.5")
        assert abs(active_bets[0].probability - Decimal("0.526")) < Decimal(
            "0.001"
        )  # 1/1.90
        assert active_bets[0].status == BetStatus.ACTIVE

    async def test_settle_winning_spread_bet(self, broker_with_agent):
        """Test settling a winning spread bet"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set odds with spread
        odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            spread_updates=[{"spread": -3.5, "home_odds": 1.90, "away_odds": 1.90}],
        )
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Place spread bet on home (-3.5)
        await broker.place_bet(
            agent.actor_id,
            BetRequestSpread(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                spread_value=Decimal("-3.5"),
            ),
        )

        # Game result: Home wins by 5 (covers -3.5 spread)
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id="test_event",
                winner="home",
                home_score=110,
                away_score=105,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Check bet settled with payout
        history = await broker.get_bet_history(agent.actor_id)
        assert len(history) == 1
        assert history[0].outcome == BetOutcome.WIN
        # Polymarket model: shares * $1.00
        # Use the actual probability from the bet (1/1.90 = 0.5263157894736842)
        bet_probability = history[0].probability
        expected_shares = Decimal("100.00") / bet_probability
        expected_payout = expected_shares * Decimal("1.00")
        assert abs(history[0].actual_payout - expected_payout) < Decimal("0.01")

    async def test_settle_losing_spread_bet(self, broker_with_agent):
        """Test settling a losing spread bet"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set odds with spread
        odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            spread_updates=[{"spread": -3.5, "home_odds": 1.90, "away_odds": 1.90}],
        )
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Place spread bet on home (-3.5)
        await broker.place_bet(
            agent.actor_id,
            BetRequestSpread(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                spread_value=Decimal("-3.5"),
            ),
        )

        # Game result: Home wins by 2 (doesn't cover -3.5 spread)
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id="test_event",
                winner="home",
                home_score=105,
                away_score=103,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Check bet settled as loss
        history = await broker.get_bet_history(agent.actor_id)
        assert len(history) == 1
        assert history[0].outcome == BetOutcome.LOSS
        assert history[0].actual_payout == Decimal("0")

    async def test_update_spread_odds(self, broker_with_agent):
        """Test updating spread odds after initial placement"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set initial spread odds
        odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            spread_updates=[{"spread": -3.5, "home_odds": 1.90, "away_odds": 1.90}],
        )
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Verify initial odds
        event = await broker.get_available_event()
        assert event is not None
        quote = event.model_dump(mode="json")
        assert "spread_lines" in quote
        assert "-3.5" in quote["spread_lines"]
        # 1/1.90 = 0.5263157894736842
        assert abs(
            Decimal(quote["spread_lines"]["-3.5"]["home_probability"])
            - Decimal("0.526")
        ) < Decimal("0.001")

        # Update spread odds
        updated_odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            spread_updates=[{"spread": -3.5, "home_odds": 1.95, "away_odds": 1.85}],
        )
        updated_odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=updated_odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(updated_odds_event)

        # Verify odds were updated
        updated_event = await broker.get_available_event()
        assert updated_event is not None
        updated_quote = updated_event.model_dump(mode="json")
        # 1/1.95 = 0.5128205128205128, 1/1.85 = 0.5405405405405406
        assert abs(
            Decimal(updated_quote["spread_lines"]["-3.5"]["home_probability"])
            - Decimal("0.513")
        ) < Decimal("0.001")
        assert abs(
            Decimal(updated_quote["spread_lines"]["-3.5"]["away_probability"])
            - Decimal("0.541")
        ) < Decimal("0.001")

    async def test_limit_order_spread_bet(self, broker_with_agent):
        """Test that limit order executes when spread odds reach threshold"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set initial spread odds
        odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            spread_updates=[{"spread": -3.5, "home_odds": 1.90, "away_odds": 1.90}],
        )
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Place limit order for spread bet
        await broker.place_bet(
            agent.actor_id,
            BetRequestSpread(
                amount=Decimal("50.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.LIMIT,
                spread_value=Decimal("-3.5"),
                limit_probability=Decimal("0.513"),  # 1/1.95 - Want better probability
            ),
        )

        # Check order is pending
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 1
        assert pending[0].status == BetStatus.PENDING

        # Update spread probabilities to trigger execution
        updated_odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            spread_updates=[
                {"spread": -3.5, "home_odds": 1.94, "away_odds": 1.86}
            ],  # home_probability = 1/1.94 = 0.515 >= 0.513 limit (should execute)
        )
        updated_odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=updated_odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(updated_odds_event)

        # Check order executed
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 0

        active = await broker.get_active_bets(agent.actor_id)
        assert len(active) == 1
        assert active[0].status == BetStatus.ACTIVE
        # 1/1.94 = 0.515464... ≈ 0.515
        assert abs(active[0].probability - Decimal("0.515")) < Decimal("0.001")
        assert active[0].bet_type == BetType.SPREAD
        assert active[0].spread_value == Decimal("-3.5")


class TestTotalBetting:
    """Test total (over/under) betting functionality"""

    async def test_place_total_over_bet(self, broker_with_agent):
        """Test placing an over bet"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set odds with totals
        odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            total_updates=[
                {"total": 220.5, "over_odds": 1.88, "under_odds": 1.88},
                {"total": 221.5, "over_odds": 1.90, "under_odds": 1.86},
            ],
        )
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Place over bet
        result = await broker.place_bet(
            agent.actor_id,
            BetRequestTotal(
                amount=Decimal("100.00"),
                selection="over",
                event_id="test_event",
                order_type=OrderType.MARKET,
                total_value=Decimal("220.5"),
            ),
        )

        assert result == "bet_placed"

        # Check bet was executed
        active_bets = await broker.get_active_bets(agent.actor_id)
        assert len(active_bets) == 1
        assert active_bets[0].bet_type == BetType.TOTAL
        assert active_bets[0].total_value == Decimal("220.5")
        assert active_bets[0].selection == "over"
        assert abs(active_bets[0].probability - Decimal("0.532")) < Decimal(
            "0.001"
        )  # 1/1.88

    async def test_settle_winning_over_bet(self, broker_with_agent):
        """Test settling a winning over bet"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set odds with totals
        odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            total_updates=[{"total": 220.5, "over_odds": 1.88, "under_odds": 1.88}],
        )
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Place over bet
        await broker.place_bet(
            agent.actor_id,
            BetRequestTotal(
                amount=Decimal("100.00"),
                selection="over",
                event_id="test_event",
                order_type=OrderType.MARKET,
                total_value=Decimal("220.5"),
            ),
        )

        # Game result: Total points = 225 (over 220.5)
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id="test_event",
                winner="home",
                home_score=115,
                away_score=110,  # Total = 225
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Check bet settled as win
        history = await broker.get_bet_history(agent.actor_id)
        assert len(history) == 1
        assert history[0].outcome == BetOutcome.WIN
        # Polymarket model: shares * $1.00
        # Use the actual probability from the bet (1/1.88 = 0.5319148936170213)
        bet_probability = history[0].probability
        expected_shares = Decimal("100.00") / bet_probability
        expected_payout = expected_shares * Decimal("1.00")
        assert abs(history[0].actual_payout - expected_payout) < Decimal("0.01")

    async def test_settle_losing_under_bet(self, broker_with_agent):
        """Test settling a losing under bet"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set odds with totals
        odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            total_updates=[{"total": 220.5, "over_odds": 1.88, "under_odds": 1.88}],
        )
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Place under bet
        await broker.place_bet(
            agent.actor_id,
            BetRequestTotal(
                amount=Decimal("100.00"),
                selection="under",
                event_id="test_event",
                order_type=OrderType.MARKET,
                total_value=Decimal("220.5"),
            ),
        )

        # Game result: Total points = 225 (over 220.5, so under loses)
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id="test_event",
                winner="home",
                home_score=115,
                away_score=110,  # Total = 225
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Check bet settled as loss
        history = await broker.get_bet_history(agent.actor_id)
        assert len(history) == 1
        assert history[0].outcome == BetOutcome.LOSS
        assert history[0].actual_payout == Decimal("0")

    async def test_update_total_odds(self, broker_with_agent):
        """Test updating total odds after initial placement"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set initial total odds
        odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            total_updates=[{"total": 220.5, "over_odds": 1.88, "under_odds": 1.88}],
        )
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Verify initial odds
        event = await broker.get_available_event()
        assert event is not None
        quote = event.model_dump(mode="json")
        assert "total_lines" in quote
        assert "220.5" in quote["total_lines"]
        # 1/1.88 = 0.5319148936170213
        assert abs(
            Decimal(quote["total_lines"]["220.5"]["over_probability"])
            - Decimal("0.532")
        ) < Decimal("0.001")

        # Update total probabilities
        updated_odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            total_updates=[{"total": 220.5, "over_odds": 1.92, "under_odds": 1.84}],
        )
        updated_odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=updated_odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(updated_odds_event)

        # Verify odds were updated
        updated_event = await broker.get_available_event()
        assert updated_event is not None
        updated_quote = updated_event.model_dump(mode="json")
        # 1/1.92 = 0.5208333333333334, 1/1.84 = 0.5434782608695652
        assert abs(
            Decimal(updated_quote["total_lines"]["220.5"]["over_probability"])
            - Decimal("0.521")
        ) < Decimal("0.001")
        assert abs(
            Decimal(updated_quote["total_lines"]["220.5"]["under_probability"])
            - Decimal("0.543")
        ) < Decimal("0.001")

    async def test_limit_order_total_bet(self, broker_with_agent):
        """Test that limit order executes when total odds reach threshold"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set initial total odds
        odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            total_updates=[{"total": 220.5, "over_odds": 1.88, "under_odds": 1.88}],
        )
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Place limit order for over bet
        await broker.place_bet(
            agent.actor_id,
            BetRequestTotal(
                amount=Decimal("50.00"),
                selection="over",
                event_id="test_event",
                order_type=OrderType.LIMIT,
                total_value=Decimal("220.5"),
                limit_probability=Decimal("0.526"),  # 1/1.90 - Want better probability
            ),
        )

        # Check order is pending
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 1
        assert pending[0].status == BetStatus.PENDING

        # Update total odds to trigger execution
        updated_odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            total_updates=[
                {"total": 220.5, "over_odds": 1.88, "under_odds": 1.84}
            ],  # over_probability = 1/1.88 = 0.532 >= 0.526 limit (should execute)
        )
        updated_odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=updated_odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(updated_odds_event)

        # Check order executed
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 0

        active = await broker.get_active_bets(agent.actor_id)
        assert len(active) == 1
        assert active[0].status == BetStatus.ACTIVE
        # 1/1.88 = 0.531915... ≈ 0.532
        assert abs(active[0].probability - Decimal("0.532")) < Decimal("0.001")
        assert active[0].bet_type == BetType.TOTAL
        assert active[0].total_value == Decimal("220.5")
        assert active[0].selection == "over"


class TestMultipleSpreadsTotals:
    """Test multiple spread and total lines"""

    async def test_multiple_spread_lines(self, broker_with_agent):
        """Test that multiple spread lines can be updated and used"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set odds with multiple spreads
        odds_payload = create_odds_event_with_spreads_totals(
            game_id="test_event",
            spread_updates=[
                {"spread": -3.5, "home_odds": 1.90, "away_odds": 1.90},
                {"spread": -4.5, "home_odds": 1.85, "away_odds": 1.95},
                {"spread": -5.5, "home_odds": 1.80, "away_odds": 2.00},
            ],
        )
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=odds_payload,
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Place bets on different spreads
        await broker.place_bet(
            agent.actor_id,
            BetRequestSpread(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                spread_value=Decimal("-3.5"),
            ),
        )

        await broker.place_bet(
            agent.actor_id,
            BetRequestSpread(
                amount=Decimal("50.00"),
                selection="away",
                event_id="test_event",
                order_type=OrderType.MARKET,
                spread_value=Decimal("-4.5"),  # Away gets +4.5
            ),
        )

        # Check both bets were placed
        active_bets = await broker.get_active_bets(agent.actor_id)
        assert len(active_bets) == 2

        # Verify different spreads and odds
        spread_35_bet = [b for b in active_bets if b.spread_value == Decimal("-3.5")][0]
        spread_45_bet = [b for b in active_bets if b.spread_value == Decimal("-4.5")][0]

        assert abs(spread_35_bet.probability - Decimal("0.526")) < Decimal(
            "0.001"
        )  # 1/1.90
        assert abs(spread_45_bet.probability - Decimal("0.513")) < Decimal(
            "0.001"
        )  # 1/1.95 (away spread probability)

    async def test_backward_compatibility_moneyline_default(self, broker_with_agent):
        """Test that moneyline betting still works without specifying bet_type"""
        broker, agent = broker_with_agent

        # Initialize event
        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init_event)

        # Set moneyline odds only (no spreads/totals)
        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                game_id="test_event",
                odds=OddsInfo(
                    moneyline=MoneylineOdds(
                        home_probability=0.513,
                        away_probability=0.476,
                        home_odds=1.95,
                        away_odds=2.10,
                    )
                ),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_event)

        # Place bet without bet_type (should default to MONEYLINE)
        result = await broker.place_bet(
            agent.actor_id,
            BetRequestMoneyline(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                # bet_type not specified - should default to MONEYLINE
            ),
        )

        assert result == "bet_placed"

        # Check bet is moneyline type
        active_bets = await broker.get_active_bets(agent.actor_id)
        assert len(active_bets) == 1
        assert active_bets[0].bet_type == BetType.MONEYLINE
        assert abs(active_bets[0].probability - Decimal("0.513")) < Decimal(
            "0.001"
        )  # 1/1.95


# =============================================================================
# Agent Tools Configuration Tests
# =============================================================================


class TestAllowedTools:
    """Test allowed_tools configuration"""

    @pytest_asyncio.fixture
    async def broker_with_limited_tools(self):
        """Create broker with limited allowed tools"""
        config: BrokerOperatorConfig = {
            "actor_id": "test_broker",
            "initial_balance": "1000.00",
            "allowed_tools": ["get_balance", "get_event", "place_market_bet_moneyline"],
        }
        broker = BrokerOperator(config, trial_id="test-trial")
        await broker.start()
        yield broker
        await broker.stop()

    async def test_allowed_tools_filtering(self, broker_with_limited_tools, agent):
        """Test that only allowed tools are exposed"""
        broker = broker_with_limited_tools
        await broker.register_agents([agent])  # type: ignore[arg-type]

        # Get tools for agent
        tools = broker.agent_tools(agent.actor_id)
        tool_names = [tool.__name__ for tool in tools]

        # Should only have the allowed tools
        assert "get_balance" in tool_names
        assert "get_event" in tool_names
        assert "place_market_bet_moneyline" in tool_names

        # Should NOT have the restricted tools
        assert "place_limit_bet_moneyline" not in tool_names
        assert "place_market_bet_spread" not in tool_names
        assert "place_limit_bet_spread" not in tool_names
        assert "place_market_bet_total" not in tool_names
        assert "place_limit_bet_total" not in tool_names
        assert "cancel_bet" not in tool_names
        assert "get_pending_orders" not in tool_names

    async def test_all_tools_when_none_specified(self, broker, agent):
        """Test that all tools are available when allowed_tools is None"""
        await broker.register_agents([agent])  # type: ignore[arg-type]

        # Get tools for agent
        tools = broker.agent_tools(agent.actor_id)
        tool_names = [tool.__name__ for tool in tools]

        # Should have all tools
        expected_tools = [
            "get_balance",
            "get_holdings",
            "get_event",
            "place_market_bet_moneyline",
            "place_limit_bet_moneyline",
            "place_market_bet_spread",
            "place_limit_bet_spread",
            "place_market_bet_total",
            "place_limit_bet_total",
            "cancel_bet",
            "get_pending_orders",
            "get_bet_history",
            "get_statistics",
        ]

        for tool_name in expected_tools:
            assert tool_name in tool_names, f"Expected tool {tool_name} not found"

        assert len(tool_names) == len(expected_tools)


# =============================================================================
# Event Ordering Tests
# =============================================================================


class TestEventOrdering:
    """Tests for handling out-of-order events (race conditions)."""

    async def test_game_start_before_game_initialize(self, broker):
        """Test that GameStartEvent arriving before GameInitializeEvent is handled correctly.

        This tests the race condition where play_by_play endpoint returns faster than
        boxscore endpoint, causing GameStartEvent to arrive before GameInitializeEvent.
        """
        event_id = "test_event"

        # 1. GameStartEvent arrives BEFORE GameInitializeEvent (out of order)
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id=event_id),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        # Event should not exist yet (GameStartEvent was buffered)
        event = await broker.get_available_event()
        assert event is None

        # 2. GameInitializeEvent arrives (normal order would be first)
        game_init = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id=event_id,
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.now(),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init)

        # 3. Verify status is LIVE (buffered GameStartEvent was applied)
        event = await broker.get_available_event()
        assert event is not None
        quote = event.model_dump(mode="json")
        assert quote["status"] == "LIVE"

    async def test_game_result_before_game_initialize(self, broker):
        """Test that GameResultEvent arriving before GameInitializeEvent is handled.

        This tests the extreme case where a finished game's result event arrives
        before the initialization event.
        """
        event_id = "test_event"

        # 1. GameStartEvent arrives first (buffered)
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id=event_id),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        # 2. GameResultEvent arrives second (also buffered)
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id=event_id,
                winner="home",
                home_score=110,
                away_score=105,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Event should not exist yet
        event = await broker.get_available_event()
        assert event is None

        # 3. GameInitializeEvent arrives last
        game_init = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id=event_id,
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.now(),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init)

        # 4. Verify status is SETTLED (both buffered events were applied in order)
        # After settlement, event is no longer available
        event = await broker.get_available_event()
        assert event is None

    async def test_normal_order_still_works(self, broker):
        """Test that normal event ordering (initialize → start → result) still works."""
        event_id = "test_event"

        # 1. GameInitializeEvent first (normal order)
        game_init = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                game_id=event_id,
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.now(),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init)

        event = await broker.get_available_event()
        assert event is not None
        quote = event.model_dump(mode="json")
        assert quote["status"] == "SCHEDULED"

        # 2. GameStartEvent second
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(game_id=event_id),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        event = await broker.get_available_event()
        assert event is not None
        quote = event.model_dump(mode="json")
        assert quote["status"] == "LIVE"

        # 3. GameResultEvent third
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                game_id=event_id,
                winner="home",
                home_score=110,
                away_score=105,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # After settlement, event is no longer available
        event = await broker.get_available_event()
        assert event is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
