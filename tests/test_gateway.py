"""Tests for Gateway module."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
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
from dojozero.gateway._auth import (
    AgentCredentials,
    AgentIdentity,
    AgentKeyEntry,
    AgentKeyManager,
    AuthConfig,
    AuthProvider,
    LocalAgentAuthenticator,
    NoOpAuthenticator,
)
from dojozero.gateway._rate_limit import (
    RateLimitBucket,
    RateLimitConfig,
    RateLimiter,
    RateLimitType,
)
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


class TestAuthProvider:
    """Tests for AuthProvider."""

    def test_validate_header_auth(self):
        """Test X-Agent-ID header authentication."""
        provider = AuthProvider()
        credentials = provider.validate_header_auth("agent1")

        assert credentials.agent_id == "agent1"
        assert not credentials.is_expired

    def test_validate_header_auth_empty(self):
        """Test empty agent ID raises 401."""
        provider = AuthProvider()

        with pytest.raises(HTTPException) as exc_info:
            provider.validate_header_auth("")

        assert exc_info.value.status_code == 401

    def test_authenticate_with_header(self):
        """Test authentication with X-Agent-ID header."""
        provider = AuthProvider()
        credentials = provider.authenticate(x_agent_id="agent1")

        assert credentials.agent_id == "agent1"

    def test_authenticate_no_credentials(self):
        """Test authentication fails without credentials."""
        provider = AuthProvider()

        with pytest.raises(HTTPException) as exc_info:
            provider.authenticate()

        assert exc_info.value.status_code == 401

    def test_register_unregister_agent(self):
        """Test agent registration tracking."""
        provider = AuthProvider()

        assert not provider.is_registered("agent1")

        provider.register_agent("agent1")
        assert provider.is_registered("agent1")

        provider.unregister_agent("agent1")
        assert not provider.is_registered("agent1")

    def test_agent_credentials_expiry(self):
        """Test credential expiry check."""
        # Non-expiring credentials
        creds = AgentCredentials(agent_id="agent1")
        assert not creds.is_expired

        # Expired credentials
        import time

        creds_expired = AgentCredentials(
            agent_id="agent1",
            expires_at=time.time() - 100,
        )
        assert creds_expired.is_expired

        # Future expiry
        creds_future = AgentCredentials(
            agent_id="agent1",
            expires_at=time.time() + 3600,
        )
        assert not creds_future.is_expired

    def test_auth_config_defaults(self):
        """Test AuthConfig default values."""
        config = AuthConfig()

        assert config.jwt_secret is None
        assert config.jwt_algorithm == "HS256"
        assert config.require_registration is True
        assert config.allow_header_auth is True


class TestAgentIdentity:
    """Tests for AgentIdentity dataclass."""

    def test_basic_creation(self):
        """Test creating an AgentIdentity with required fields."""
        identity = AgentIdentity(agent_id="agent_alice")

        assert identity.agent_id == "agent_alice"
        assert identity.display_name is None
        assert identity.metadata is None

    def test_full_creation(self):
        """Test creating an AgentIdentity with all fields."""
        identity = AgentIdentity(
            agent_id="agent_alice",
            display_name="Alice's Bot",
            metadata={"org": "team-alpha", "tier": "premium"},
        )

        assert identity.agent_id == "agent_alice"
        assert identity.display_name == "Alice's Bot"
        assert identity.metadata == {"org": "team-alpha", "tier": "premium"}

    def test_to_dict(self):
        """Test converting AgentIdentity to dict."""
        identity = AgentIdentity(
            agent_id="agent_alice",
            display_name="Alice's Bot",
            metadata={"org": "team-alpha"},
        )

        result = identity.to_dict()

        assert result["agentId"] == "agent_alice"
        assert result["displayName"] == "Alice's Bot"
        assert result["metadata"] == {"org": "team-alpha"}

    def test_from_dict_camel_case(self):
        """Test creating AgentIdentity from camelCase dict."""
        data = {
            "agentId": "agent_bob",
            "displayName": "Bob's Agent",
            "metadata": {"tier": "basic"},
        }

        identity = AgentIdentity.from_dict(data)

        assert identity.agent_id == "agent_bob"
        assert identity.display_name == "Bob's Agent"
        assert identity.metadata == {"tier": "basic"}

    def test_from_dict_snake_case(self):
        """Test creating AgentIdentity from snake_case dict."""
        data = {
            "agent_id": "agent_charlie",
            "display_name": "Charlie's Agent",
        }

        identity = AgentIdentity.from_dict(data)

        assert identity.agent_id == "agent_charlie"
        assert identity.display_name == "Charlie's Agent"

    def test_immutable(self):
        """Test that AgentIdentity is immutable (frozen)."""
        identity = AgentIdentity(agent_id="agent_alice")

        with pytest.raises(AttributeError):
            identity.agent_id = "agent_bob"  # type: ignore


class TestLocalAgentAuthenticator:
    """Tests for LocalAgentAuthenticator."""

    @pytest.mark.asyncio
    async def test_validate_with_direct_keys(self):
        """Test validation with directly provided keys."""
        keys = {
            "sk-agent-abc123": AgentIdentity(
                agent_id="agent_alice",
                display_name="Alice's Bot",
            ),
            "sk-agent-def456": AgentIdentity(agent_id="agent_bob"),
        }
        auth = LocalAgentAuthenticator(keys=keys)

        # Valid key
        identity = await auth.validate("sk-agent-abc123")
        assert identity is not None
        assert identity.agent_id == "agent_alice"
        assert identity.display_name == "Alice's Bot"

        # Another valid key
        identity = await auth.validate("sk-agent-def456")
        assert identity is not None
        assert identity.agent_id == "agent_bob"

        # Invalid key
        identity = await auth.validate("sk-agent-invalid")
        assert identity is None

    @pytest.mark.asyncio
    async def test_validate_empty_authenticator(self):
        """Test validation with no keys configured."""
        auth = LocalAgentAuthenticator()

        identity = await auth.validate("sk-agent-any")
        assert identity is None

    def test_is_enabled_with_keys(self):
        """Test is_enabled returns True when keys are configured."""
        auth = LocalAgentAuthenticator(keys={"sk-key": AgentIdentity(agent_id="agent")})
        assert auth.is_enabled() is True

    def test_is_enabled_without_keys(self):
        """Test is_enabled returns False when no keys configured."""
        auth = LocalAgentAuthenticator()
        assert auth.is_enabled() is False

    def test_add_and_remove_key(self):
        """Test adding and removing keys dynamically."""
        auth = LocalAgentAuthenticator()

        assert not auth.is_enabled()

        # Add key
        auth.add_key("sk-new-key", AgentIdentity(agent_id="new_agent"))
        assert auth.is_enabled()

        # Remove key
        result = auth.remove_key("sk-new-key")
        assert result is True
        assert not auth.is_enabled()

        # Remove non-existent key
        result = auth.remove_key("sk-non-existent")
        assert result is False

    @pytest.mark.asyncio
    async def test_load_from_yaml_file(self, tmp_path):
        """Test loading keys from YAML file."""
        # Create a temporary YAML file
        yaml_content = """
agents:
  sk-agent-yaml1:
    agent_id: agent_from_yaml
    display_name: YAML Agent
    metadata:
      source: yaml_file
  sk-agent-yaml2: simple_agent
"""
        yaml_file = tmp_path / "agent_keys.yaml"
        yaml_file.write_text(yaml_content)

        auth = LocalAgentAuthenticator(config_path=yaml_file)

        # Check full format
        identity = await auth.validate("sk-agent-yaml1")
        assert identity is not None
        assert identity.agent_id == "agent_from_yaml"
        assert identity.display_name == "YAML Agent"
        assert identity.metadata == {"source": "yaml_file"}

        # Check simple format
        identity = await auth.validate("sk-agent-yaml2")
        assert identity is not None
        assert identity.agent_id == "simple_agent"
        assert identity.display_name is None

    @pytest.mark.asyncio
    async def test_load_from_nonexistent_file(self, tmp_path):
        """Test that nonexistent file doesn't cause error."""
        nonexistent = tmp_path / "does_not_exist.yaml"

        auth = LocalAgentAuthenticator(config_path=nonexistent)

        assert not auth.is_enabled()
        identity = await auth.validate("any-key")
        assert identity is None


class TestNoOpAuthenticator:
    """Tests for NoOpAuthenticator."""

    @pytest.mark.asyncio
    async def test_validate_always_returns_none(self):
        """Test that NoOpAuthenticator always returns None."""
        auth = NoOpAuthenticator()

        identity = await auth.validate("any-key")
        assert identity is None

        identity = await auth.validate("")
        assert identity is None

    def test_is_enabled_always_false(self):
        """Test that NoOpAuthenticator is never enabled."""
        auth = NoOpAuthenticator()
        assert auth.is_enabled() is False


class TestAgentKeyEntry:
    """Tests for AgentKeyEntry dataclass."""

    def test_from_yaml_data_simple_format(self):
        """Test creating entry from simple string format."""
        entry = AgentKeyEntry.from_yaml_data("sk-key-123", "my_agent_id")

        assert entry.api_key == "sk-key-123"
        assert entry.identity.agent_id == "my_agent_id"
        assert entry.identity.display_name is None
        assert entry.identity.metadata is None

    def test_from_yaml_data_full_format(self):
        """Test creating entry from full dict format."""
        data = {
            "agent_id": "full_agent",
            "display_name": "Full Agent Name",
            "metadata": {"tier": "premium"},
        }
        entry = AgentKeyEntry.from_yaml_data("sk-key-456", data)

        assert entry.api_key == "sk-key-456"
        assert entry.identity.agent_id == "full_agent"
        assert entry.identity.display_name == "Full Agent Name"
        assert entry.identity.metadata == {"tier": "premium"}

    def test_to_yaml_data(self):
        """Test converting entry to YAML-compatible dict."""
        identity = AgentIdentity(
            agent_id="test_agent",
            display_name="Test Agent",
            metadata={"org": "team-x"},
        )
        entry = AgentKeyEntry(api_key="sk-test", identity=identity)

        data = entry.to_yaml_data()

        assert data["agent_id"] == "test_agent"
        assert data["display_name"] == "Test Agent"
        assert data["metadata"] == {"org": "team-x"}

    def test_to_yaml_data_minimal(self):
        """Test converting minimal entry (no display_name/metadata)."""
        identity = AgentIdentity(agent_id="minimal_agent")
        entry = AgentKeyEntry(api_key="sk-minimal", identity=identity)

        data = entry.to_yaml_data()

        assert data == {"agent_id": "minimal_agent"}
        assert "display_name" not in data
        assert "metadata" not in data


class TestAgentKeyManager:
    """Tests for AgentKeyManager."""

    @pytest.fixture
    def keys_file(self, tmp_path):
        """Create temporary keys file path."""
        return tmp_path / "agent_keys.yaml"

    def test_empty_manager(self, keys_file):
        """Test manager with no keys file."""
        manager = AgentKeyManager(keys_file)

        assert len(manager) == 0
        assert not manager
        assert manager.get("any-key") is None
        assert manager.find_by_agent_id("any-agent") is None

    def test_add_and_get(self, keys_file):
        """Test adding and retrieving keys."""
        manager = AgentKeyManager(keys_file)

        manager.add("sk-new", "new_agent", display_name="New Agent")

        assert len(manager) == 1
        assert manager

        identity = manager.get("sk-new")
        assert identity is not None
        assert identity.agent_id == "new_agent"
        assert identity.display_name == "New Agent"

    def test_add_persists_to_file(self, keys_file):
        """Test that add persists to YAML file."""
        import yaml

        manager = AgentKeyManager(keys_file)
        manager.add("sk-persist", "persist_agent")

        # Verify file was created
        assert keys_file.exists()

        # Verify content
        with open(keys_file) as f:
            data = yaml.safe_load(f)

        assert "sk-persist" in data["agents"]
        assert data["agents"]["sk-persist"]["agent_id"] == "persist_agent"

    def test_find_by_agent_id(self, keys_file):
        """Test finding entry by agent_id."""
        manager = AgentKeyManager(keys_file)
        manager.add("sk-findme", "searchable_agent")

        entry = manager.find_by_agent_id("searchable_agent")

        assert entry is not None
        assert entry.api_key == "sk-findme"
        assert entry.identity.agent_id == "searchable_agent"

        # Not found
        assert manager.find_by_agent_id("nonexistent") is None

    def test_remove(self, keys_file):
        """Test removing keys."""
        manager = AgentKeyManager(keys_file)
        manager.add("sk-removeme", "remove_agent")

        assert len(manager) == 1

        result = manager.remove("sk-removeme")
        assert result is True
        assert len(manager) == 0

        # Remove again returns False
        result = manager.remove("sk-removeme")
        assert result is False

    def test_remove_by_agent_id(self, keys_file):
        """Test removing by agent_id."""
        manager = AgentKeyManager(keys_file)
        manager.add("sk-byagent", "agent_to_remove")

        result = manager.remove_by_agent_id("agent_to_remove")
        assert result is True
        assert len(manager) == 0

        # Not found
        result = manager.remove_by_agent_id("nonexistent")
        assert result is False

    def test_list_all(self, keys_file):
        """Test listing all entries."""
        manager = AgentKeyManager(keys_file)
        manager.add("sk-a", "agent_a")
        manager.add("sk-b", "agent_b", display_name="Agent B")

        entries = manager.list_all()

        assert len(entries) == 2
        agent_ids = {e.identity.agent_id for e in entries}
        assert agent_ids == {"agent_a", "agent_b"}

    def test_load_existing_file(self, keys_file):
        """Test loading from existing YAML file."""
        import yaml

        # Create YAML file
        data = {
            "agents": {
                "sk-existing1": {
                    "agent_id": "existing_agent",
                    "display_name": "Existing",
                },
                "sk-existing2": "simple_agent",
            }
        }
        with open(keys_file, "w") as f:
            yaml.safe_dump(data, f)

        # Load
        manager = AgentKeyManager(keys_file)

        assert len(manager) == 2

        id1 = manager.get("sk-existing1")
        assert id1 is not None
        assert id1.agent_id == "existing_agent"
        assert id1.display_name == "Existing"

        id2 = manager.get("sk-existing2")
        assert id2 is not None
        assert id2.agent_id == "simple_agent"

    def test_reload(self, keys_file):
        """Test reloading keys from file."""
        import yaml

        manager = AgentKeyManager(keys_file)
        manager.add("sk-initial", "initial_agent")

        # Manually modify file
        with open(keys_file) as f:
            data = yaml.safe_load(f)
        data["agents"]["sk-new"] = {"agent_id": "new_agent"}
        with open(keys_file, "w") as f:
            yaml.safe_dump(data, f)

        # Before reload
        assert manager.get("sk-new") is None

        # After reload
        manager.reload()
        assert manager.get("sk-new") is not None

    def test_generate_api_key(self):
        """Test API key generation."""
        key1 = AgentKeyManager.generate_api_key()
        key2 = AgentKeyManager.generate_api_key()

        assert key1.startswith("sk-agent-")
        assert key2.startswith("sk-agent-")
        assert len(key1) == len("sk-agent-") + 32  # 16 bytes hex = 32 chars
        assert key1 != key2

    def test_to_identity_dict(self, keys_file):
        """Test converting to identity dict."""
        manager = AgentKeyManager(keys_file)
        manager.add("sk-dict1", "agent1")
        manager.add("sk-dict2", "agent2", display_name="Agent 2")

        identity_dict = manager.to_identity_dict()

        assert len(identity_dict) == 2
        assert identity_dict["sk-dict1"].agent_id == "agent1"
        assert identity_dict["sk-dict2"].agent_id == "agent2"
        assert identity_dict["sk-dict2"].display_name == "Agent 2"

    def test_creates_parent_directory(self, tmp_path):
        """Test that save creates parent directories."""
        nested_path = tmp_path / "nested" / "dir" / "agent_keys.yaml"

        manager = AgentKeyManager(nested_path)
        manager.add("sk-nested", "nested_agent")

        assert nested_path.exists()


class TestRateLimiter:
    """Tests for RateLimiter."""

    def test_rate_limit_bucket_consume(self):
        """Test token bucket consumption."""
        bucket = RateLimitBucket(tokens=10, capacity=10, refill_rate=1.0)

        assert bucket.consume(5)
        assert bucket.tokens == pytest.approx(5, abs=0.1)

        assert bucket.consume(5)
        assert bucket.tokens == pytest.approx(0, abs=0.1)

        assert not bucket.consume(1)

    def test_rate_limit_bucket_refill(self):
        """Test token bucket refill."""
        import time

        bucket = RateLimitBucket(tokens=0, capacity=10, refill_rate=10.0)
        bucket.last_refill = time.time() - 1  # 1 second ago

        # Should refill 10 tokens (10/sec * 1 sec)
        bucket._refill()
        assert bucket.tokens == 10

    def test_rate_limit_bucket_retry_after(self):
        """Test retry_after calculation."""
        bucket = RateLimitBucket(tokens=0, capacity=10, refill_rate=1.0)

        # Need 1 token at 1 token/sec = 1 second
        assert bucket.retry_after > 0
        assert bucket.retry_after <= 1.0

    def test_rate_limiter_check_general(self):
        """Test general rate limiting."""
        config = RateLimitConfig(general_rpm=5, window_seconds=60)
        limiter = RateLimiter(config)

        # Should allow first 5 requests
        for _ in range(5):
            limiter.check_rate_limit("agent1", RateLimitType.GENERAL)

        # 6th request should be rate limited
        with pytest.raises(HTTPException) as exc_info:
            limiter.check_rate_limit("agent1", RateLimitType.GENERAL)

        assert exc_info.value.status_code == 429

    def test_rate_limiter_check_bet(self):
        """Test bet rate limiting."""
        config = RateLimitConfig(bet_rpm=2, window_seconds=60)
        limiter = RateLimiter(config)

        limiter.check_rate_limit("agent1", RateLimitType.BET)
        limiter.check_rate_limit("agent1", RateLimitType.BET)

        with pytest.raises(HTTPException) as exc_info:
            limiter.check_rate_limit("agent1", RateLimitType.BET)

        assert exc_info.value.status_code == 429

    def test_rate_limiter_sse_connections(self):
        """Test SSE connection limiting."""
        config = RateLimitConfig(max_sse_connections=2)
        limiter = RateLimiter(config)

        limiter.acquire_sse_connection("agent1")
        limiter.acquire_sse_connection("agent1")

        with pytest.raises(HTTPException) as exc_info:
            limiter.acquire_sse_connection("agent1")

        assert exc_info.value.status_code == 429

        # Release one and try again
        limiter.release_sse_connection("agent1")
        limiter.acquire_sse_connection("agent1")  # Should succeed

    def test_rate_limiter_disabled(self):
        """Test rate limiting can be disabled."""
        config = RateLimitConfig(general_rpm=1, enabled=False)
        limiter = RateLimiter(config)

        # Should not raise even though limit is 1
        for _ in range(100):
            limiter.check_rate_limit("agent1", RateLimitType.GENERAL)

    def test_rate_limiter_per_agent(self):
        """Test rate limits are per-agent."""
        config = RateLimitConfig(general_rpm=2, window_seconds=60)
        limiter = RateLimiter(config)

        # agent1 uses 2 requests
        limiter.check_rate_limit("agent1", RateLimitType.GENERAL)
        limiter.check_rate_limit("agent1", RateLimitType.GENERAL)

        # agent2 should still have quota
        limiter.check_rate_limit("agent2", RateLimitType.GENERAL)

    def test_rate_limiter_stats(self):
        """Test rate limit stats."""
        config = RateLimitConfig(general_rpm=10, bet_rpm=5, max_sse_connections=3)
        limiter = RateLimiter(config)

        # New agent gets default stats
        stats = limiter.get_stats("unknown")
        assert stats["general_remaining"] == 10
        assert stats["bet_remaining"] == 5
        assert stats["sse_connections"] == 0

        # Use some quota
        limiter.check_rate_limit("agent1", RateLimitType.GENERAL)
        limiter.acquire_sse_connection("agent1")

        stats = limiter.get_stats("agent1")
        assert stats["general_remaining"] == 9
        assert stats["sse_connections"] == 1
        assert stats["sse_remaining"] == 2

    def test_rate_limiter_reset(self):
        """Test rate limit reset."""
        config = RateLimitConfig(general_rpm=2, window_seconds=60)
        limiter = RateLimiter(config)

        limiter.check_rate_limit("agent1", RateLimitType.GENERAL)
        limiter.check_rate_limit("agent1", RateLimitType.GENERAL)

        # Should be rate limited
        with pytest.raises(HTTPException):
            limiter.check_rate_limit("agent1", RateLimitType.GENERAL)

        # Reset and try again
        limiter.reset("agent1")
        limiter.check_rate_limit("agent1", RateLimitType.GENERAL)  # Should succeed

    def test_rate_limit_config_defaults(self):
        """Test RateLimitConfig default values."""
        config = RateLimitConfig()

        assert config.general_rpm == 300
        assert config.bet_rpm == 60
        assert config.max_sse_connections == 5
        assert config.window_seconds == 60
        assert config.enabled is True


class TestTrialResults:
    """Tests for trial results and trial_ended features."""

    @pytest.fixture
    def mock_data_hub(self):
        """Create mock DataHub."""
        hub = MagicMock()
        hub.subscription_manager = MagicMock()
        hub.subscription_manager.global_sequence = 100
        hub.subscription_manager.subscribe = AsyncMock()
        hub.subscription_manager.broadcast = AsyncMock()
        return hub

    @pytest.fixture
    def mock_broker(self):
        """Create mock BrokerOperator with accounts."""
        broker = MagicMock()
        broker.initial_balance = "1000"
        broker.create_account = AsyncMock()
        broker._event = None
        # Mock accounts with balances
        broker._accounts = {
            "agent1": MagicMock(balance=Decimal("1200")),
            "agent2": MagicMock(balance=Decimal("800")),
        }

        # Mock get_statistics to return proper stats
        async def mock_get_stats(agent_id):
            stats = MagicMock()
            if agent_id == "agent1":
                stats.net_profit = Decimal("200")
                stats.total_bets = 2
                stats.win_rate = 0.5
                stats.roi = 0.2
            else:
                stats.net_profit = Decimal("-200")
                stats.total_bets = 1
                stats.win_rate = 0.0
                stats.roi = -0.2
            return stats

        broker.get_statistics = mock_get_stats
        return broker

    @pytest.fixture
    def adapter(self, mock_data_hub, mock_broker):
        """Create adapter with mocks."""
        adapter = ExternalAgentAdapter(
            data_hub=mock_data_hub,
            broker=mock_broker,
            trial_id="trial123",
        )
        # Pre-register agents by adding to _agents dict with proper state objects
        adapter._agents["agent1"] = ExternalAgentState(
            agent_id="agent1",
            display_name="Agent One",
            authenticated=True,
        )
        adapter._agents["agent2"] = ExternalAgentState(
            agent_id="agent2",
            display_name=None,
            authenticated=False,
        )
        return adapter

    @pytest.mark.asyncio
    async def test_get_results_running_trial(self, adapter):
        """Test get_results returns running status when trial not ended."""
        results = await adapter.get_results()

        assert results.trial_id == "trial123"
        assert results.status == "running"
        assert results.ended_at is None
        assert len(results.results) == 2

    @pytest.mark.asyncio
    async def test_get_results_after_trial_ended(self, adapter):
        """Test get_results returns correct status after signal_trial_ended."""
        await adapter.signal_trial_ended(reason="completed", message="Game over")

        results = await adapter.get_results()

        assert results.trial_id == "trial123"
        assert results.status == "completed"
        assert results.ended_at is not None
        assert len(results.results) == 2

    @pytest.mark.asyncio
    async def test_signal_trial_ended_sets_event_and_message(self, adapter):
        """Test signal_trial_ended sets the event and stores the message."""
        assert not adapter._trial_ended_event.is_set()
        assert adapter._trial_ended_message is None

        await adapter.signal_trial_ended(reason="completed", message="Game finished")

        # Verify event is set and message is stored
        assert adapter._trial_ended_event.is_set()
        assert adapter._trial_ended_message is not None
        assert adapter._trial_ended_message.type == "trial_ended"
        assert adapter._trial_ended_message.trial_id == "trial123"
        assert adapter._trial_ended_message.reason == "completed"
        assert adapter._trial_ended_message.message == "Game finished"
        assert len(adapter._trial_ended_message.final_results) == 2

    @pytest.mark.asyncio
    async def test_signal_trial_ended_only_once(self, adapter):
        """Test signal_trial_ended can only be called once."""
        await adapter.signal_trial_ended(reason="completed", message="First")
        first_message = adapter._trial_ended_message

        await adapter.signal_trial_ended(reason="failed", message="Second")

        # Message should not change after second call
        assert adapter._trial_ended_message is first_message
        assert adapter._trial_ended_message.reason == "completed"

    @pytest.mark.asyncio
    async def test_results_include_agent_stats(self, adapter):
        """Test results include proper agent statistics."""
        results = await adapter.get_results()

        # Find agent1 results (should be first due to higher balance)
        agent1_result = next(r for r in results.results if r.agent_id == "agent1")
        assert agent1_result.final_balance == "1200"
        assert agent1_result.total_bets == 2
        assert agent1_result.win_rate == 0.5

        # Find agent2 results
        agent2_result = next(r for r in results.results if r.agent_id == "agent2")
        assert agent2_result.final_balance == "800"
        assert agent2_result.total_bets == 1


class TestGatewayAuthIntegration:
    """Tests for gateway registration with authentication."""

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
        broker.create_account = AsyncMock()
        return broker

    @pytest.fixture
    def authenticator(self):
        """Create LocalAgentAuthenticator with test keys."""
        return LocalAgentAuthenticator(
            keys={
                "sk-valid-key-123": AgentIdentity(
                    agent_id="verified_agent",
                    display_name="Verified Agent Name",
                    metadata={"tier": "premium"},
                ),
                "sk-valid-key-456": AgentIdentity(
                    agent_id="another_verified",
                ),
            }
        )

    @pytest.fixture
    def app_with_auth(self, mock_data_hub, mock_broker, authenticator):
        """Create test app with authentication enabled."""
        return create_gateway_app(
            trial_id="trial123",
            data_hub=mock_data_hub,
            broker=mock_broker,
            metadata={"sport_type": "nba"},
            authenticator=authenticator,
        )

    @pytest.fixture
    def client_with_auth(self, app_with_auth):
        """Create test client with auth-enabled app."""
        with TestClient(app_with_auth) as client:
            yield client

    @pytest.fixture
    def app_no_auth(self, mock_data_hub, mock_broker):
        """Create test app with authentication disabled (NoOpAuthenticator)."""
        return create_gateway_app(
            trial_id="trial123",
            data_hub=mock_data_hub,
            broker=mock_broker,
            metadata={"sport_type": "nba"},
            authenticator=NoOpAuthenticator(),
        )

    @pytest.fixture
    def client_no_auth(self, app_no_auth):
        """Create test client with no-auth app."""
        with TestClient(app_no_auth) as client:
            yield client

    def test_register_with_valid_api_key(self, client_with_auth):
        """Test registration with valid API key succeeds and returns verified identity."""
        response = client_with_auth.post(
            "/api/v1/register",
            json={
                "agentId": "my_claimed_id",  # Will be overridden by verified ID
                "apiKey": "sk-valid-key-123",
                "displayName": "My Custom Name",  # Can override verified display_name
            },
        )

        assert response.status_code == 200
        data = response.json()
        # Uses verified agent_id from authenticator
        assert data["agentId"] == "verified_agent"
        assert data["trialId"] == "trial123"
        assert "balance" in data

    def test_register_with_invalid_api_key(self, client_with_auth):
        """Test registration with invalid API key returns 401."""
        response = client_with_auth.post(
            "/api/v1/register",
            json={
                "agentId": "agent1",
                "apiKey": "sk-invalid-key",
            },
        )

        assert response.status_code == 401

    def test_register_without_api_key_auth_enabled(self, client_with_auth):
        """Test registration without API key when auth is enabled returns 401."""
        response = client_with_auth.post(
            "/api/v1/register",
            json={
                "agentId": "agent1",
            },
        )

        assert response.status_code == 401

    def test_register_without_api_key_auth_disabled(self, client_no_auth):
        """Test registration without API key when auth is disabled succeeds."""
        response = client_no_auth.post(
            "/api/v1/register",
            json={
                "agentId": "unauthenticated_agent",
                "displayName": "Test Agent",
            },
        )

        assert response.status_code == 200
        data = response.json()
        # Uses the agent_id from request
        assert data["agentId"] == "unauthenticated_agent"

    def test_register_verified_agent_uses_identity_display_name(self, client_with_auth):
        """Test that verified agent can fall back to identity's display_name."""
        response = client_with_auth.post(
            "/api/v1/register",
            json={
                "agentId": "any_id",
                "apiKey": "sk-valid-key-123",
                # Not providing displayName - should use identity's display_name
            },
        )

        assert response.status_code == 200
        # The display_name from identity should be used

    def test_multiple_agents_different_auth_status(self, client_with_auth):
        """Test registering multiple agents with different auth status."""
        # Register verified agent
        response1 = client_with_auth.post(
            "/api/v1/register",
            json={
                "agentId": "claimed_id_1",
                "apiKey": "sk-valid-key-123",
            },
        )
        assert response1.status_code == 200
        assert response1.json()["agentId"] == "verified_agent"

        # Register another verified agent
        response2 = client_with_auth.post(
            "/api/v1/register",
            json={
                "agentId": "claimed_id_2",
                "apiKey": "sk-valid-key-456",
            },
        )
        assert response2.status_code == 200
        assert response2.json()["agentId"] == "another_verified"

    def test_duplicate_registration_same_verified_agent(self, client_with_auth):
        """Test that same verified agent cannot register twice."""
        # First registration
        response1 = client_with_auth.post(
            "/api/v1/register",
            json={
                "agentId": "any",
                "apiKey": "sk-valid-key-123",
            },
        )
        assert response1.status_code == 200

        # Second registration with same API key (same verified_agent)
        response2 = client_with_auth.post(
            "/api/v1/register",
            json={
                "agentId": "different",
                "apiKey": "sk-valid-key-123",
            },
        )
        assert response2.status_code == 409  # Conflict - already registered
