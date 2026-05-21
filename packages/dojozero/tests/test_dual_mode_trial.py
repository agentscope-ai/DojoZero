"""Tests for dual-mode trial: same game with betting + prediction gateways.

Verifies that an external agent can simultaneously participate in two
independent trials (one classic betting, one prediction) for the same game.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from dojozero.gateway._server import create_gateway_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_data_hub():
    """Create mock DataHub shared by both trials."""
    hub = MagicMock()
    hub.subscription_manager = MagicMock()
    hub.subscription_manager.global_sequence = 100
    hub.subscription_manager.subscribe = AsyncMock()
    hub.subscription_manager.unsubscribe = AsyncMock(return_value=True)
    hub.get_recent_events.return_value = []
    hub.get_events_since.return_value = []
    return hub


@pytest.fixture
def mock_betting_broker():
    """Create mock BrokerOperator for classic betting mode."""
    from dojozero.betting._broker import BrokerOperator

    broker = MagicMock(spec=BrokerOperator)
    broker.initial_balance = "1000"
    broker.create_account = AsyncMock()
    broker.delete_account = AsyncMock(return_value=True)
    broker.has_account = MagicMock(return_value=False)
    broker.get_contest_kind.return_value = "classic_betting"
    broker.get_rules.return_value = {
        "kind": "classic_betting",
        "description": "Place market or limit bets via the standard betting tools.",
    }
    broker.is_accepting.return_value = True
    broker._event = MagicMock()
    broker._event.event_id = "game-123"
    broker._event.can_bet = True
    broker._event.home_team = "Lakers"
    broker._event.away_team = "Celtics"
    broker._event.game_time = datetime(2026, 5, 20, tzinfo=timezone.utc)
    broker._event.home_probability = Decimal("0.55")
    broker._event.away_probability = Decimal("0.45")
    broker._event.spread_lines = {}
    broker._event.total_lines = {}
    broker._event.last_odds_update = None
    broker._accounts = {}
    broker._bets = {}
    broker._active_bets = {}
    broker._pending_orders = {}
    broker._bet_history = {}
    return broker


@pytest.fixture
def mock_prediction_broker():
    """Create mock PredictionBroker for prediction mode.

    Must be an instance of PredictionBroker for isinstance checks to pass.
    """
    from dojozero.betting._prediction_broker import PredictionBroker

    broker = MagicMock(spec=PredictionBroker)
    broker.get_contest_kind.return_value = "window_pool_prediction"
    broker.is_accepting.return_value = False
    broker.agents = set()
    broker._event = MagicMock()
    broker._event.event_id = "game-123"
    broker._event.home_team = "Lakers"
    broker._event.away_team = "Celtics"
    broker._event.game_time = datetime(2026, 5, 20, tzinfo=timezone.utc)
    broker._event.can_bet = False
    broker.get_rules.return_value = {
        "kind": "window_pool_prediction",
        "num_windows": 5,
        "window_pools": [5000, 4000, 3000, 2000, 500],
        "selections": ["home_win", "away_win", "even"],
    }
    broker.get_prediction_statistics.return_value = {}
    broker.submit_prediction = AsyncMock(return_value="prediction_submitted")
    broker.get_my_predictions = AsyncMock(return_value=[])
    broker.get_event_info = AsyncMock(
        return_value={
            "event_id": "game-123",
            "home_team": "Lakers",
            "away_team": "Celtics",
            "status": "in_progress",
            "current_window": 1,
            "elapsed_ratio": 0.25,
        }
    )
    return broker


@pytest.fixture
def betting_app(mock_data_hub, mock_betting_broker):
    """Create betting mode gateway app."""
    return create_gateway_app(
        trial_id="nba-betting-game123",
        data_hub=mock_data_hub,
        broker=mock_betting_broker,
        metadata={"sport_type": "nba", "espn_game_id": "game-123"},
    )


@pytest.fixture
def prediction_app(mock_data_hub, mock_prediction_broker):
    """Create prediction mode gateway app."""
    return create_gateway_app(
        trial_id="nba-prediction-game123",
        data_hub=mock_data_hub,
        broker=mock_prediction_broker,
        metadata={"sport_type": "nba", "espn_game_id": "game-123"},
    )


@pytest.fixture
def betting_client(betting_app):
    """TestClient for betting gateway."""
    with TestClient(betting_app) as client:
        yield client


@pytest.fixture
def prediction_client(prediction_app):
    """TestClient for prediction gateway."""
    with TestClient(prediction_app) as client:
        yield client


# ---------------------------------------------------------------------------
# Tests: Dual Mode Health & Mode Detection
# ---------------------------------------------------------------------------


class TestDualModeHealth:
    """Verify both gateways report correct mode."""

    def test_betting_health_mode(self, betting_client):
        resp = betting_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "classic_betting"
        assert data["trial_id"] == "nba-betting-game123"

    def test_prediction_health_mode(self, prediction_client):
        resp = prediction_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "prediction"
        assert data["trial_id"] == "nba-prediction-game123"

    def test_betting_rules(self, betting_client):
        resp = betting_client.get("/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "classic_betting"

    def test_prediction_rules(self, prediction_client):
        resp = prediction_client.get("/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "window_pool_prediction"
        assert data["num_windows"] == 5


# ---------------------------------------------------------------------------
# Tests: Same Agent Registers on Both Gateways
# ---------------------------------------------------------------------------


class TestDualModeRegistration:
    """Verify same agent can register on both gateways simultaneously."""

    def test_same_agent_registers_on_both(
        self, betting_client, prediction_client, mock_betting_broker
    ):
        mock_betting_broker.create_account = AsyncMock()

        # Register on betting gateway
        resp1 = betting_client.post("/agents", json={"apiKey": "agent1"})
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["agentId"] == "agent1"
        assert data1["trialId"] == "nba-betting-game123"

        # Register on prediction gateway (same agent_id)
        resp2 = prediction_client.post("/agents", json={"apiKey": "agent1"})
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["agentId"] == "agent1"
        assert data2["trialId"] == "nba-prediction-game123"

    def test_independent_session_keys(
        self, betting_client, prediction_client, mock_betting_broker
    ):
        """Each gateway issues its own session key."""
        mock_betting_broker.create_account = AsyncMock()

        resp1 = betting_client.post("/agents", json={"apiKey": "agent1"})
        resp2 = prediction_client.post("/agents", json={"apiKey": "agent1"})

        sk1 = resp1.json()["sessionKey"]
        sk2 = resp2.json()["sessionKey"]
        assert sk1 != sk2


# ---------------------------------------------------------------------------
# Tests: Mode-Exclusive Endpoints
# ---------------------------------------------------------------------------


class TestDualModeEndpointExclusivity:
    """Verify betting endpoints are blocked on prediction gateway and vice versa."""

    def _register_agent(self, client, mock_betting_broker=None):
        if mock_betting_broker:
            mock_betting_broker.create_account = AsyncMock()
        client.post("/agents", json={"apiKey": "agent1"})

    def test_betting_endpoints_blocked_on_prediction(
        self, prediction_client, mock_betting_broker
    ):
        """Prediction gateway returns 404 for betting-only endpoints."""
        self._register_agent(prediction_client)

        # /odds/current should be blocked
        resp = prediction_client.get("/odds/current", headers={"X-Agent-ID": "agent1"})
        assert resp.status_code == 404

        # POST /bets should be blocked
        resp = prediction_client.post(
            "/bets",
            json={"market": "moneyline", "selection": "home", "amount": "100"},
            headers={"X-Agent-ID": "agent1"},
        )
        assert resp.status_code == 404

        # GET /bets should be blocked
        resp = prediction_client.get("/bets", headers={"X-Agent-ID": "agent1"})
        assert resp.status_code == 404

        # GET /balance should be blocked
        resp = prediction_client.get("/balance", headers={"X-Agent-ID": "agent1"})
        assert resp.status_code == 404

    def test_prediction_endpoints_blocked_on_betting(
        self, betting_client, mock_betting_broker
    ):
        """Betting gateway returns 404 for prediction-only endpoints."""
        self._register_agent(betting_client, mock_betting_broker)

        # POST /predictions should be blocked
        resp = betting_client.post(
            "/predictions",
            json={"selection": "home_win"},
            headers={"X-Agent-ID": "agent1"},
        )
        assert resp.status_code == 404

        # GET /predictions should be blocked
        resp = betting_client.get("/predictions", headers={"X-Agent-ID": "agent1"})
        assert resp.status_code == 404

        # GET /event/info should be blocked
        resp = betting_client.get("/event/info", headers={"X-Agent-ID": "agent1"})
        assert resp.status_code == 404

    def test_betting_endpoints_work_on_betting_gateway(
        self, betting_client, mock_betting_broker
    ):
        """Betting gateway accepts betting operations."""
        self._register_agent(betting_client, mock_betting_broker)

        # GET /odds/current should work
        resp = betting_client.get("/odds/current", headers={"X-Agent-ID": "agent1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["bettingOpen"] is True
        assert data["eventId"] == "game-123"

    def test_prediction_endpoints_work_on_prediction_gateway(
        self, prediction_client, mock_prediction_broker
    ):
        """Prediction gateway accepts prediction operations."""
        self._register_agent(prediction_client)

        # GET /event/info should work
        resp = prediction_client.get("/event/info", headers={"X-Agent-ID": "agent1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["eventId"] == "game-123"
        assert data["currentWindow"] == 1


# ---------------------------------------------------------------------------
# Tests: Concurrent Betting and Prediction Operations
# ---------------------------------------------------------------------------


class TestDualModeConcurrentOperations:
    """Verify betting and prediction can be performed concurrently."""

    def _register_on_both(self, betting_client, prediction_client, mock_betting_broker):
        mock_betting_broker.create_account = AsyncMock()
        betting_client.post("/agents", json={"apiKey": "agent1"})
        prediction_client.post("/agents", json={"apiKey": "agent1"})

    def test_get_odds_then_event_info(
        self,
        betting_client,
        prediction_client,
        mock_betting_broker,
        mock_prediction_broker,
    ):
        """Agent can query odds on betting and event info on prediction."""
        self._register_on_both(betting_client, prediction_client, mock_betting_broker)

        # Query betting odds
        resp_odds = betting_client.get(
            "/odds/current", headers={"X-Agent-ID": "agent1"}
        )
        assert resp_odds.status_code == 200
        odds_data = resp_odds.json()
        assert odds_data["homeProbability"] == 0.55

        # Query prediction event info
        resp_info = prediction_client.get(
            "/event/info", headers={"X-Agent-ID": "agent1"}
        )
        assert resp_info.status_code == 200
        info_data = resp_info.json()
        assert info_data["homeTeam"] == "Lakers"
        assert info_data["awayTeam"] == "Celtics"

    def test_leaderboard_mode_consistency(
        self,
        betting_client,
        prediction_client,
        mock_betting_broker,
        mock_prediction_broker,
    ):
        """Each gateway's leaderboard reflects its mode."""
        self._register_on_both(betting_client, prediction_client, mock_betting_broker)

        # Betting leaderboard
        resp_bet_lb = betting_client.get("/leaderboard")
        assert resp_bet_lb.status_code == 200
        bet_lb = resp_bet_lb.json()
        assert bet_lb["mode"] == "classic_betting"

        # Prediction leaderboard
        resp_pred_lb = prediction_client.get("/leaderboard")
        assert resp_pred_lb.status_code == 200
        pred_lb = resp_pred_lb.json()
        assert pred_lb["mode"] == "prediction"


# ---------------------------------------------------------------------------
# Tests: Trial Metadata
# ---------------------------------------------------------------------------


class TestDualModeMetadata:
    """Verify trial metadata is correct for both gateways."""

    def test_betting_trial_metadata(self, betting_client):
        resp = betting_client.get("/trial")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trialId"] == "nba-betting-game123"

    def test_prediction_trial_metadata(self, prediction_client):
        resp = prediction_client.get("/trial")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trialId"] == "nba-prediction-game123"
