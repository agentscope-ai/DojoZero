"""Tests for Gateway module."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from dojozero.gateway._models import (
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    BalanceResponse,
    BetRequest,
    BetResponse,
    CurrentOddsResponse,
    ErrorCodes,
    ErrorDetail,
    ErrorResponse,
    EventEnvelope,
    HeartbeatMessage,
    HoldingResponse,
    SpreadLine,
    TotalLine,
)
from dojozero.gateway._adapter import ExternalAgentAdapter, ExternalAgentState
from dojozero.gateway._sse import SSEConnection
from dojozero.gateway._server import create_gateway_app


class TestModels:
    """Tests for Pydantic models."""

    def test_agent_registration_request(self):
        """Test AgentRegistrationRequest with camelCase aliases."""
        request = AgentRegistrationRequest(
            agentId="agent1",
            persona="test persona",
            model="gpt-4",
            initialBalance="1000",
        )
        assert request.agent_id == "agent1"
        assert request.persona == "test persona"
        assert request.model == "gpt-4"
        assert request.initial_balance == "1000"

    def test_agent_registration_request_from_camel_case(self):
        """Test AgentRegistrationRequest parsing camelCase JSON."""
        data = {
            "agentId": "agent1",
            "persona": "test",
            "initialBalance": "500",
        }
        request = AgentRegistrationRequest.model_validate(data)
        assert request.agent_id == "agent1"
        assert request.initial_balance == "500"

    def test_agent_registration_response_serialization(self):
        """Test AgentRegistrationResponse serializes to camelCase."""
        response = AgentRegistrationResponse(
            agent_id="agent1",
            trial_id="trial123",
            balance="1000",
            registered_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        dumped = response.model_dump(by_alias=True)
        assert "agentId" in dumped
        assert "trialId" in dumped
        assert "registeredAt" in dumped

    def test_bet_request(self):
        """Test BetRequest with all fields."""
        request = BetRequest(
            market="moneyline",
            selection="home",
            amount="100",
            orderType="market",
            referenceSequence=42,
            idempotencyKey="unique-key-123",
        )
        assert request.market == "moneyline"
        assert request.selection == "home"
        assert request.amount == "100"
        assert request.reference_sequence == 42

    def test_bet_response_serialization(self):
        """Test BetResponse serializes to camelCase."""
        response = BetResponse(
            bet_id="bet123",
            agent_id="agent1",
            event_id="game456",
            market="moneyline",
            selection="home",
            amount="100",
            probability="0.55",
            shares="181.82",
            status="filled",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        dumped = response.model_dump(by_alias=True)
        assert "betId" in dumped
        assert "agentId" in dumped
        assert "eventId" in dumped
        assert "createdAt" in dumped

    def test_current_odds_response(self):
        """Test CurrentOddsResponse with spread/total lines."""
        response = CurrentOddsResponse(
            event_id="game123",
            home_probability=0.55,
            away_probability=0.45,
            spread_lines={
                "-5.5": SpreadLine(home_probability=0.5, away_probability=0.5)
            },
            total_lines={
                "220.5": TotalLine(over_probability=0.48, under_probability=0.52)
            },
            betting_open=True,
        )
        assert response.event_id == "game123"
        assert response.betting_open is True
        assert "-5.5" in response.spread_lines
        assert "220.5" in response.total_lines

    def test_event_envelope(self):
        """Test EventEnvelope structure."""
        envelope = EventEnvelope(
            trial_id="trial123",
            sequence=42,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            payload={"event_type": "event.nba_play", "data": {}},
        )
        assert envelope.type == "event"
        assert envelope.sequence == 42
        dumped = envelope.model_dump(by_alias=True)
        assert "trialId" in dumped

    def test_error_response(self):
        """Test ErrorResponse structure."""
        error = ErrorResponse(
            error=ErrorDetail(
                code=ErrorCodes.BET_REJECTED,
                message="Insufficient balance",
                details={"available": 50, "requested": 100},
            )
        )
        assert error.error.code == "BET_REJECTED"
        assert error.error.message == "Insufficient balance"

    def test_balance_response(self):
        """Test BalanceResponse with holdings."""
        response = BalanceResponse(
            agent_id="agent1",
            balance="950",
            holdings=[
                HoldingResponse(
                    event_id="game123",
                    selection="home",
                    bet_type="moneyline",
                    shares="50",
                    avg_probability="0.55",
                )
            ],
        )
        assert response.balance == "950"
        assert len(response.holdings) == 1
        assert response.holdings[0].selection == "home"

    def test_heartbeat_message(self):
        """Test HeartbeatMessage."""
        hb = HeartbeatMessage(timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc))
        assert hb.type == "heartbeat"


class TestExternalAgentState:
    """Tests for ExternalAgentState."""

    def test_creation(self):
        """Test state creation with defaults."""
        state = ExternalAgentState(agent_id="agent1")
        assert state.agent_id == "agent1"
        assert state.subscription is None
        assert state.registered_at is not None
        assert state.last_activity_at is not None


class TestExternalAgentAdapter:
    """Tests for ExternalAgentAdapter."""

    @pytest.fixture
    def mock_data_hub(self):
        """Create mock DataHub."""
        hub = MagicMock()
        hub.subscription_manager = MagicMock()
        hub.subscription_manager.global_sequence = 100
        hub.subscription_manager.subscribe = AsyncMock()
        hub.subscription_manager.unsubscribe = AsyncMock(return_value=True)
        return hub

    @pytest.fixture
    def mock_broker(self):
        """Create mock BrokerOperator."""
        broker = MagicMock()
        broker.initial_balance = "1000"
        broker.create_account = AsyncMock()
        broker._event = None
        broker._accounts = {}
        broker._bets = {}
        broker._active_bets = {}
        broker._pending_orders = {}
        broker._bet_history = {}
        return broker

    @pytest.fixture
    def adapter(self, mock_data_hub, mock_broker):
        """Create adapter with mocks."""
        return ExternalAgentAdapter(
            data_hub=mock_data_hub,
            broker=mock_broker,
            trial_id="trial123",
        )

    @pytest.mark.asyncio
    async def test_register_agent(self, adapter, mock_broker):
        """Test agent registration."""
        response = await adapter.register_agent(
            agent_id="agent1",
            persona="test persona",
            initial_balance="500",
        )

        assert response.agent_id == "agent1"
        assert response.trial_id == "trial123"
        assert response.balance == "500"
        mock_broker.create_account.assert_called_once_with("agent1", Decimal("500"))

    @pytest.mark.asyncio
    async def test_register_duplicate_agent(self, adapter):
        """Test duplicate registration raises error."""
        await adapter.register_agent(agent_id="agent1")

        with pytest.raises(ValueError, match="already registered"):
            await adapter.register_agent(agent_id="agent1")

    @pytest.mark.asyncio
    async def test_unregister_agent(self, adapter):
        """Test agent unregistration."""
        await adapter.register_agent(agent_id="agent1")
        assert adapter.is_registered("agent1")

        result = await adapter.unregister_agent("agent1")
        assert result is True
        assert not adapter.is_registered("agent1")

    @pytest.mark.asyncio
    async def test_unregister_nonexistent(self, adapter):
        """Test unregistering nonexistent agent."""
        result = await adapter.unregister_agent("nonexistent")
        assert result is False

    def test_is_registered(self, adapter):
        """Test registration check."""
        assert not adapter.is_registered("agent1")

    @pytest.mark.asyncio
    async def test_subscribe_creates_subscription(self, adapter, mock_data_hub):
        """Test subscription creation."""
        mock_subscription = MagicMock()
        mock_data_hub.subscription_manager.subscribe.return_value = mock_subscription

        await adapter.register_agent(agent_id="agent1")
        subscription = await adapter.subscribe(
            agent_id="agent1",
            event_types=["event.nba_*"],
        )

        assert subscription == mock_subscription
        mock_data_hub.subscription_manager.subscribe.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscribe_unregistered_agent(self, adapter):
        """Test subscribing unregistered agent raises error."""
        with pytest.raises(ValueError, match="not registered"):
            await adapter.subscribe(agent_id="unknown")

    def test_get_current_odds_no_event(self, adapter):
        """Test odds when no event."""
        odds = adapter.get_current_odds()
        assert odds.event_id == ""
        assert odds.betting_open is False

    def test_get_current_odds_with_event(self, adapter, mock_broker):
        """Test odds with active event."""
        mock_event = MagicMock()
        mock_event.event_id = "game123"
        mock_event.home_probability = Decimal("0.55")
        mock_event.away_probability = Decimal("0.45")
        mock_event.spread_lines = {}
        mock_event.total_lines = {}
        mock_event.last_odds_update = datetime.now(timezone.utc)
        mock_event.can_bet = True
        mock_broker._event = mock_event

        odds = adapter.get_current_odds()
        assert odds.event_id == "game123"
        assert odds.home_probability == 0.55
        assert odds.betting_open is True


class TestSSEConnection:
    """Tests for SSEConnection."""

    @pytest.fixture
    def mock_subscription(self):
        """Create mock subscription."""
        sub = MagicMock()
        sub.subscription_id = "sub123"
        sub.get_next_sequence.return_value = 1
        return sub

    def test_format_sse(self):
        """Test SSE message formatting."""
        result = SSEConnection._format_sse(
            event="event",
            data={"key": "value"},
            id="123",
        )
        assert "id: 123" in result
        assert "event: event" in result
        assert 'data: {"key": "value"}' in result
        assert result.endswith("\n\n")

    def test_format_sse_with_retry(self):
        """Test SSE message with retry."""
        result = SSEConnection._format_sse(
            event="heartbeat",
            data={},
            retry=5000,
        )
        assert "retry: 5000" in result


class TestGatewayServer:
    """Tests for Gateway FastAPI server."""

    @pytest.fixture
    def mock_data_hub(self):
        """Create mock DataHub."""
        hub = MagicMock()
        hub.subscription_manager = MagicMock()
        hub.subscription_manager.global_sequence = 100
        hub.get_recent_events.return_value = []
        return hub

    @pytest.fixture
    def mock_broker(self):
        """Create mock BrokerOperator."""
        broker = MagicMock()
        broker.initial_balance = "1000"
        broker._event = None
        broker._accounts = {}
        return broker

    @pytest.fixture
    def app(self, mock_data_hub, mock_broker):
        """Create test app."""
        return create_gateway_app(
            trial_id="trial123",
            data_hub=mock_data_hub,
            broker=mock_broker,
            metadata={"sport_type": "nba"},
        )

    @pytest.fixture
    def client(self, app):
        """Create test client with lifespan."""
        with TestClient(app) as client:
            yield client

    def test_health_check(self, client):
        """Test health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["trial_id"] == "trial123"

    def test_register_agent(self, client, mock_broker):
        """Test agent registration endpoint."""
        mock_broker.create_account = AsyncMock()

        response = client.post(
            "/api/v1/register",
            json={
                "agentId": "agent1",
                "persona": "test",
                "initialBalance": "500",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["agentId"] == "agent1"
        assert data["trialId"] == "trial123"

    def test_register_duplicate(self, client, mock_broker):
        """Test duplicate registration returns 409."""
        mock_broker.create_account = AsyncMock()

        # First registration
        client.post("/api/v1/register", json={"agentId": "agent1"})

        # Duplicate
        response = client.post("/api/v1/register", json={"agentId": "agent1"})
        assert response.status_code == 409

    def test_unregister_agent(self, client, mock_broker):
        """Test agent unregistration."""
        mock_broker.create_account = AsyncMock()

        # Register first
        client.post("/api/v1/register", json={"agentId": "agent1"})

        # Unregister
        response = client.delete("/api/v1/register/agent1")
        assert response.status_code == 200

    def test_unregister_nonexistent(self, client):
        """Test unregistering nonexistent agent."""
        response = client.delete("/api/v1/register/unknown")
        assert response.status_code == 404

    def test_get_trial_metadata(self, client):
        """Test trial metadata endpoint."""
        response = client.get("/api/v1/trial")
        assert response.status_code == 200
        data = response.json()
        assert data["trialId"] == "trial123"

    def test_get_odds_requires_registration(self, client):
        """Test odds endpoint requires agent registration."""
        response = client.get(
            "/api/v1/odds/current",
            headers={"X-Agent-ID": "unknown"},
        )
        assert response.status_code == 403

    def test_get_odds_requires_auth(self, client):
        """Test odds endpoint requires X-Agent-ID header."""
        response = client.get("/api/v1/odds/current")
        assert response.status_code == 401

    def test_get_balance_requires_registration(self, client):
        """Test balance endpoint requires registration."""
        response = client.get(
            "/api/v1/balance",
            headers={"X-Agent-ID": "unknown"},
        )
        assert response.status_code == 403

    def test_place_bet_requires_auth(self, client):
        """Test bet placement requires auth."""
        response = client.post(
            "/api/v1/bets",
            json={
                "market": "moneyline",
                "selection": "home",
                "amount": "100",
            },
        )
        assert response.status_code == 401
