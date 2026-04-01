"""Tests for daemon module."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dojozero_client._client import AgentResult, EventEnvelope, TrialEndedEvent
from dojozero_client._daemon import (
    DaemonState,
    TrialHandler,
    _trial_state_dir,
    get_daemon_status,
    is_daemon_running,
    stop_daemon,
)
from dojozero_client._config import CONFIG_DIR


class TestTrialStateDir:
    """Tests for _trial_state_dir function."""

    def test_returns_path_under_config_dir(self):
        """Test state dir is under ~/.dojozero/trials/{trial_id}/."""
        result = _trial_state_dir("my-trial-123")
        assert result == CONFIG_DIR / "trials" / "my-trial-123"

    def test_different_trials_get_different_dirs(self):
        """Test different trials have different state directories."""
        dir1 = _trial_state_dir("trial-1")
        dir2 = _trial_state_dir("trial-2")
        assert dir1 != dir2
        assert dir1.name == "trial-1"
        assert dir2.name == "trial-2"


class TestDaemonState:
    """Tests for DaemonState."""

    def test_to_dict(self):
        """Test serialization to dict."""
        state = DaemonState(
            trial_id="test-trial",
            agent_id="agent-1",
            session_key="sk-session-123",
            status="connected",
            balance=1000.0,
            last_event_sequence=42,
        )
        data = state.to_dict()
        assert data["trial_id"] == "test-trial"
        assert data["agent_id"] == "agent-1"
        assert data["session_key"] == "sk-session-123"
        assert data["status"] == "connected"
        assert data["balance"] == 1000.0
        assert data["last_event_sequence"] == 42
        assert "gateway_url" not in data

    def test_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "trial_id": "test-trial",
            "agent_id": "agent-1",
            "session_key": "sk-session-456",
            "status": "connected",
            "balance": 500.0,
            "last_event_sequence": 100,
        }
        state = DaemonState.from_dict(data)
        assert state.trial_id == "test-trial"
        assert state.agent_id == "agent-1"
        assert state.session_key == "sk-session-456"
        assert state.status == "connected"
        assert state.balance == 500.0
        assert state.last_event_sequence == 100

    def test_from_dict_defaults(self):
        """Test deserialization with minimal data uses defaults."""
        data = {
            "trial_id": "test-trial",
            "status": "connected",
        }
        state = DaemonState.from_dict(data)
        assert state.trial_id == "test-trial"
        assert state.session_key == ""  # Default empty string


class TestDaemonHelpers:
    """Tests for daemon helper functions."""

    def test_get_daemon_status_no_state_file(self):
        """Test get_daemon_status returns None when no state file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_daemon_status(state_dir=Path(tmpdir))
            assert result is None

    def test_get_daemon_status_with_state_file(self):
        """Test get_daemon_status reads state file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            state_file = state_dir / "state.json"
            state_file.write_text(
                json.dumps({"trial_id": "test", "status": "connected"})
            )

            result = get_daemon_status(state_dir=state_dir)
            assert result is not None
            assert result["trial_id"] == "test"
            assert result["status"] == "connected"

    def test_get_daemon_status_with_trial_id(self):
        """Test get_daemon_status with trial_id computes state_dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create trial-specific dir
            trial_dir = Path(tmpdir) / "trials" / "my-trial"
            trial_dir.mkdir(parents=True)
            state_file = trial_dir / "state.json"
            state_file.write_text(json.dumps({"trial_id": "my-trial"}))

            with patch("dojozero_client._daemon.CONFIG_DIR", Path(tmpdir)):
                result = get_daemon_status(trial_id="my-trial")
                assert result is not None
                assert result["trial_id"] == "my-trial"

    def test_is_daemon_running_no_pid_file(self):
        """Test is_daemon_running returns False when no PID file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            with patch("dojozero_client._daemon.PID_FILE", pid_file):
                result = is_daemon_running()
                assert result is False

    def test_is_daemon_running_stale_pid(self):
        """Test is_daemon_running returns False for stale PID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            pid_file.write_text("999999999")  # Non-existent PID

            with patch("dojozero_client._daemon.PID_FILE", pid_file):
                result = is_daemon_running()
                assert result is False

    def test_is_daemon_running_current_process(self):
        """Test is_daemon_running returns True for current process PID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            pid_file.write_text(str(os.getpid()))

            with patch("dojozero_client._daemon.PID_FILE", pid_file):
                result = is_daemon_running()
                assert result is True

    def test_stop_daemon_no_pid_file(self):
        """Test stop_daemon returns False when no PID file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            with patch("dojozero_client._daemon.PID_FILE", pid_file):
                result = stop_daemon()
                assert result is False


class TestTrialHandlerGetStatus:
    """Tests for TrialHandler.get_status() balance refresh."""

    @pytest.mark.asyncio
    async def test_get_status_refreshes_balance_when_connected(self):
        """Test get_status fetches fresh balance from server when connected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("dojozero_client._daemon.TRIALS_DIR", Path(tmpdir)):
                client = MagicMock()
                handler = TrialHandler(
                    trial_id="test-trial",
                    api_key="test-key",
                    client=client,
                )

                # Set initial cached state
                handler._state.balance = 1000.0
                handler._state.status = "connected"

                # Mock the trial connection with fresh balance
                mock_holding = MagicMock()
                mock_holding.event_id = "event-1"
                mock_holding.selection = "home"
                mock_holding.bet_type = "moneyline"
                mock_holding.shares = 10.0

                mock_balance = MagicMock()
                mock_balance.balance = 750.0  # Server has different balance
                mock_balance.holdings = [mock_holding]

                mock_trial = AsyncMock()
                mock_trial.get_balance = AsyncMock(return_value=mock_balance)
                handler._trial = mock_trial

                # Call get_status
                status = await handler.get_status()

                # Should have refreshed balance from server
                assert status["balance"] == 750.0
                assert handler._state.balance == 750.0
                assert len(status["holdings"]) == 1
                assert status["holdings"][0]["event_id"] == "event-1"

    @pytest.mark.asyncio
    async def test_get_status_returns_cached_when_not_connected(self):
        """Test get_status returns cached state when not connected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("dojozero_client._daemon.TRIALS_DIR", Path(tmpdir)):
                client = MagicMock()
                handler = TrialHandler(
                    trial_id="test-trial",
                    api_key="test-key",
                    client=client,
                )

                # Set cached state
                handler._state.balance = 1000.0
                handler._state.status = "disconnected"
                handler._trial = None  # Not connected

                # Call get_status
                status = await handler.get_status()

                # Should return cached balance
                assert status["balance"] == 1000.0

    @pytest.mark.asyncio
    async def test_get_status_returns_cached_on_refresh_failure(self):
        """Test get_status returns cached state when refresh fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("dojozero_client._daemon.TRIALS_DIR", Path(tmpdir)):
                client = MagicMock()
                handler = TrialHandler(
                    trial_id="test-trial",
                    api_key="test-key",
                    client=client,
                )

                # Set cached state
                handler._state.balance = 1000.0
                handler._state.status = "connected"

                # Mock trial that raises on get_balance
                mock_trial = AsyncMock()
                mock_trial.get_balance = AsyncMock(
                    side_effect=Exception("Connection error")
                )
                handler._trial = mock_trial

                # Call get_status - should not raise
                status = await handler.get_status()

                # Should return cached balance
                assert status["balance"] == 1000.0


def _make_agent_result(**overrides: object) -> AgentResult:
    """Create an AgentResult with sensible defaults."""
    defaults = {
        "agent_id": "agent-1",
        "final_balance": 1150.0,
        "net_profit": 150.0,
        "total_bets": 5,
        "win_rate": 0.8,
        "roi": 0.15,
    }
    defaults.update(overrides)
    return AgentResult(**defaults)  # type: ignore[arg-type]


def _make_trial_ended(
    reason: str = "completed",
    results: list[AgentResult] | None = None,
) -> TrialEndedEvent:
    """Create a TrialEndedEvent with sensible defaults."""
    if results is None:
        results = [_make_agent_result()]
    return TrialEndedEvent(
        trial_id="test-trial",
        reason=reason,
        timestamp=datetime.now(timezone.utc),
        final_results=results,
        message=f"Trial has {reason}",
    )


class TestTrialHandlerTrialEnd:
    """Tests for TrialHandler handling trial_ended events."""

    @pytest.mark.asyncio
    async def test_event_loop_handles_completed_trial(self):
        """Test event loop sets status and writes results on trial completion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("dojozero_client._daemon.TRIALS_DIR", Path(tmpdir)):
                client = MagicMock()
                handler = TrialHandler(
                    trial_id="test-trial",
                    api_key="test-key",
                    client=client,
                )
                handler._state.status = "connected"
                handler.state_dir.mkdir(parents=True, exist_ok=True)

                # Mock trial that yields one event then ends
                mock_trial = AsyncMock()
                ended = _make_trial_ended("completed")

                async def mock_events(**kwargs):
                    event = EventEnvelope(
                        trial_id="test-trial",
                        sequence=1,
                        timestamp=datetime.now(timezone.utc),
                        payload={"event_type": "event.odds_update"},
                    )
                    yield event

                mock_trial.events = mock_events
                mock_trial.trial_ended = ended
                handler._trial = mock_trial
                handler._running = True

                await handler._event_loop()

                assert handler._state.status == "completed"

                # Check results.json was written
                results_file = handler.state_dir / "results.json"
                assert results_file.exists()
                data = json.loads(results_file.read_text())
                assert data["trial_id"] == "test-trial"
                assert data["status"] == "completed"
                assert len(data["results"]) == 1
                assert data["results"][0]["agent_id"] == "agent-1"
                assert data["results"][0]["final_balance"] == 1150.0

    @pytest.mark.asyncio
    async def test_event_loop_handles_cancelled_trial(self):
        """Test event loop sets status=cancelled when trial is cancelled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("dojozero_client._daemon.TRIALS_DIR", Path(tmpdir)):
                client = MagicMock()
                handler = TrialHandler(
                    trial_id="test-trial",
                    api_key="test-key",
                    client=client,
                )
                handler._state.status = "connected"
                handler.state_dir.mkdir(parents=True, exist_ok=True)

                mock_trial = AsyncMock()
                ended = _make_trial_ended("cancelled")

                async def mock_events(**kwargs):
                    return
                    yield  # make it an async generator

                mock_trial.events = mock_events
                mock_trial.trial_ended = ended
                handler._trial = mock_trial
                handler._running = True

                await handler._event_loop()

                assert handler._state.status == "cancelled"

    @pytest.mark.asyncio
    async def test_event_loop_handles_failed_trial(self):
        """Test event loop sets status=failed when trial fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("dojozero_client._daemon.TRIALS_DIR", Path(tmpdir)):
                client = MagicMock()
                handler = TrialHandler(
                    trial_id="test-trial",
                    api_key="test-key",
                    client=client,
                )
                handler._state.status = "connected"
                handler.state_dir.mkdir(parents=True, exist_ok=True)

                mock_trial = AsyncMock()
                ended = _make_trial_ended("failed", results=[])

                async def mock_events(**kwargs):
                    return
                    yield

                mock_trial.events = mock_events
                mock_trial.trial_ended = ended
                handler._trial = mock_trial
                handler._running = True

                await handler._event_loop()

                assert handler._state.status == "failed"
                # No results.json when final_results is empty
                assert not (handler.state_dir / "results.json").exists()

    @pytest.mark.asyncio
    async def test_event_loop_no_trial_ended(self):
        """Test event loop does not change status when stream ends without trial_ended."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("dojozero_client._daemon.TRIALS_DIR", Path(tmpdir)):
                client = MagicMock()
                handler = TrialHandler(
                    trial_id="test-trial",
                    api_key="test-key",
                    client=client,
                )
                handler._state.status = "connected"
                handler.state_dir.mkdir(parents=True, exist_ok=True)

                mock_trial = AsyncMock()

                async def mock_events(**kwargs):
                    return
                    yield

                mock_trial.events = mock_events
                mock_trial.trial_ended = None  # No trial_ended event
                handler._trial = mock_trial
                handler._running = True

                await handler._event_loop()

                # Status should not be changed by event loop
                assert handler._state.status == "connected"
                assert not (handler.state_dir / "results.json").exists()

    @pytest.mark.asyncio
    async def test_event_loop_real_error_sets_error_status(self):
        """Test real exceptions still set status=error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("dojozero_client._daemon.TRIALS_DIR", Path(tmpdir)):
                client = MagicMock()
                handler = TrialHandler(
                    trial_id="test-trial",
                    api_key="test-key",
                    client=client,
                )
                handler._state.status = "connected"
                handler.state_dir.mkdir(parents=True, exist_ok=True)

                mock_trial = AsyncMock()

                async def mock_events(**kwargs):
                    raise ConnectionError("Network failed")
                    yield  # make it an async generator

                mock_trial.events = mock_events
                mock_trial.trial_ended = None
                handler._trial = mock_trial
                handler._running = True

                await handler._event_loop()

                assert handler._state.status == "error"

    @pytest.mark.asyncio
    async def test_results_json_has_all_agents(self):
        """Test results.json includes all agents from the trial."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("dojozero_client._daemon.TRIALS_DIR", Path(tmpdir)):
                client = MagicMock()
                handler = TrialHandler(
                    trial_id="test-trial",
                    api_key="test-key",
                    client=client,
                )
                handler._state.status = "connected"
                handler.state_dir.mkdir(parents=True, exist_ok=True)

                results = [
                    _make_agent_result(agent_id="alice", final_balance=1200.0, roi=0.2),
                    _make_agent_result(
                        agent_id="bob",
                        final_balance=800.0,
                        net_profit=-200.0,
                        roi=-0.2,
                    ),
                ]
                ended = _make_trial_ended("completed", results=results)

                mock_trial = AsyncMock()

                async def mock_events(**kwargs):
                    return
                    yield

                mock_trial.events = mock_events
                mock_trial.trial_ended = ended
                handler._trial = mock_trial
                handler._running = True

                await handler._event_loop()

                data = json.loads((handler.state_dir / "results.json").read_text())
                assert len(data["results"]) == 2
                assert data["results"][0]["agent_id"] == "alice"
                assert data["results"][0]["final_balance"] == 1200.0
                assert data["results"][1]["agent_id"] == "bob"
                assert data["results"][1]["net_profit"] == -200.0
