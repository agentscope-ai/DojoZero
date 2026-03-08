"""Tests for daemon module."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


from dojozero_client._daemon import (
    DaemonConfig,
    DaemonState,
    _trial_state_dir,
    get_daemon_status,
    is_daemon_running,
    is_unified_daemon_running,
    list_running_trials,
    stop_daemon,
    stop_unified_daemon,
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


class TestDaemonConfig:
    """Tests for DaemonConfig."""

    def test_auto_computes_state_dir_from_trial_id(self):
        """Test state_dir is auto-computed from trial_id."""
        config = DaemonConfig(trial_id="test-trial")
        assert config.state_dir == _trial_state_dir("test-trial")

    def test_explicit_state_dir_overrides_auto(self):
        """Test explicit state_dir is not overwritten."""
        custom_dir = Path("/custom/path")
        config = DaemonConfig(trial_id="test-trial", state_dir=custom_dir)
        assert config.state_dir == custom_dir

    def test_default_gateway_url(self):
        """Test default gateway URL."""
        config = DaemonConfig(trial_id="test")
        assert config.gateway_url == "http://localhost:8080"


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
            gateway_url="http://localhost:8000/api/trials/test-trial",
        )
        data = state.to_dict()
        assert data["trial_id"] == "test-trial"
        assert data["agent_id"] == "agent-1"
        assert data["session_key"] == "sk-session-123"
        assert data["status"] == "connected"
        assert data["balance"] == 1000.0
        assert data["last_event_sequence"] == 42
        assert data["gateway_url"] == "http://localhost:8000/api/trials/test-trial"

    def test_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "trial_id": "test-trial",
            "agent_id": "agent-1",
            "session_key": "sk-session-456",
            "status": "connected",
            "balance": 500.0,
            "last_event_sequence": 100,
            "gateway_url": "http://localhost:8080/api/trials/test-trial",
        }
        state = DaemonState.from_dict(data)
        assert state.trial_id == "test-trial"
        assert state.agent_id == "agent-1"
        assert state.session_key == "sk-session-456"
        assert state.status == "connected"
        assert state.balance == 500.0
        assert state.last_event_sequence == 100
        assert state.gateway_url == "http://localhost:8080/api/trials/test-trial"

    def test_from_dict_without_gateway_url(self):
        """Test deserialization without gateway_url or session_key (backward compat)."""
        data = {
            "trial_id": "test-trial",
            "status": "connected",
        }
        state = DaemonState.from_dict(data)
        assert state.trial_id == "test-trial"
        assert state.gateway_url == ""  # Default empty string
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
            result = is_daemon_running(state_dir=Path(tmpdir))
            assert result is False

    def test_is_daemon_running_stale_pid(self):
        """Test is_daemon_running returns False for stale PID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            pid_file = state_dir / "daemon.pid"
            # Use a PID that definitely doesn't exist
            pid_file.write_text("999999999")

            result = is_daemon_running(state_dir=state_dir)
            assert result is False

    def test_is_daemon_running_current_process(self):
        """Test is_daemon_running returns True for current process."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            pid_file = state_dir / "daemon.pid"
            # Use current process PID
            pid_file.write_text(str(os.getpid()))

            result = is_daemon_running(state_dir=state_dir)
            assert result is True

    def test_stop_daemon_no_pid_file(self):
        """Test stop_daemon returns False when no PID file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = stop_daemon(state_dir=Path(tmpdir))
            assert result is False


class TestListRunningTrials:
    """Tests for list_running_trials function."""

    def test_no_trials_dir(self):
        """Test returns empty list when trials dir doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("dojozero_client._daemon.CONFIG_DIR", Path(tmpdir)):
                result = list_running_trials()
                assert result == []

    def test_no_running_trials(self):
        """Test returns empty list when no daemons running."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trials_dir = Path(tmpdir) / "trials"
            trials_dir.mkdir()
            # Create trial dirs but no PID files
            (trials_dir / "trial-1").mkdir()
            (trials_dir / "trial-2").mkdir()

            with patch("dojozero_client._daemon.CONFIG_DIR", Path(tmpdir)):
                result = list_running_trials()
                assert result == []

    def test_lists_running_trials(self):
        """Test lists trials with active PIDs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trials_dir = Path(tmpdir) / "trials"
            trials_dir.mkdir()

            # Create running trial (current process PID)
            trial1_dir = trials_dir / "trial-1"
            trial1_dir.mkdir()
            (trial1_dir / "daemon.pid").write_text(str(os.getpid()))

            # Create stopped trial (stale PID)
            trial2_dir = trials_dir / "trial-2"
            trial2_dir.mkdir()
            (trial2_dir / "daemon.pid").write_text("999999999")

            with patch("dojozero_client._daemon.CONFIG_DIR", Path(tmpdir)):
                result = list_running_trials()
                assert result == ["trial-1"]


class TestUnifiedDaemonHelpers:
    """Tests for unified daemon helper functions."""

    def test_is_unified_daemon_running_no_pid_file(self):
        """Test returns False when no PID file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            with patch("dojozero_client._daemon.PID_FILE", pid_file):
                result = is_unified_daemon_running()
                assert result is False

    def test_is_unified_daemon_running_stale_pid(self):
        """Test returns False for stale PID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            pid_file.write_text("999999999")  # Non-existent PID

            with patch("dojozero_client._daemon.PID_FILE", pid_file):
                result = is_unified_daemon_running()
                assert result is False

    def test_is_unified_daemon_running_current_process(self):
        """Test returns True for current process PID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            pid_file.write_text(str(os.getpid()))

            with patch("dojozero_client._daemon.PID_FILE", pid_file):
                result = is_unified_daemon_running()
                assert result is True

    def test_stop_unified_daemon_no_pid_file(self):
        """Test returns False when no PID file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            with patch("dojozero_client._daemon.PID_FILE", pid_file):
                result = stop_unified_daemon()
                assert result is False

    def test_stop_unified_daemon_stale_pid(self):
        """Test returns False for stale PID (process doesn't exist)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            pid_file.write_text("999999999")  # Non-existent PID

            with patch("dojozero_client._daemon.PID_FILE", pid_file):
                result = stop_unified_daemon()
                # Returns False because os.kill fails
                assert result is False
