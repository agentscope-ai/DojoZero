"""Tests for CLI agents command."""

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# Import the function under test
from dojozero.cli import _agents_command


class TestAgentsCommand:
    """Tests for the 'dojo0 agents' CLI command."""

    @pytest.fixture
    def temp_store(self, tmp_path):
        """Create a temporary store directory."""
        store_dir = tmp_path / "dojozero-store"
        store_dir.mkdir()
        return store_dir

    @pytest.fixture
    def keys_file(self, temp_store):
        """Return path to agent_keys.yaml."""
        return temp_store / "agent_keys.yaml"

    def _make_args(
        self,
        temp_store: Path,
        agents_command: str,
        agent_id: str | None = None,
        name: str | None = None,
        persona: str | None = None,
        model: str | None = None,
        model_display_name: str | None = None,
        cdn_url: str | None = None,
        json_output: bool = False,
        yes: bool = False,
    ) -> argparse.Namespace:
        """Create argparse.Namespace for agents command."""
        args = argparse.Namespace()
        args.store = str(temp_store)
        args.agents_command = agents_command

        # 'add' command args
        if agents_command == "add":
            args.id = agent_id
            args.name = name
            args.persona = persona
            args.model = model
            args.model_display_name = model_display_name
            args.cdn_url = cdn_url

        # 'list' command args
        if agents_command == "list":
            args.json = json_output

        # 'remove' command args
        if agents_command == "remove":
            args.agent_id = agent_id
            args.yes = yes

        return args

    def test_add_agent(self, temp_store, keys_file, capsys):
        """Test adding a new agent."""
        args = self._make_args(temp_store, "add", agent_id="test_agent")

        with patch("secrets.token_hex", return_value="abc123"):
            result = _agents_command(args)

        assert result == 0
        assert keys_file.exists()

        # Verify the agent was added
        with open(keys_file) as f:
            data = yaml.safe_load(f)

        assert "agents" in data
        api_key = "sk-agent-abc123"
        assert api_key in data["agents"]
        assert data["agents"][api_key]["agent_id"] == "test_agent"

        # Check output
        captured = capsys.readouterr()
        assert "Created agent 'test_agent'" in captured.out
        assert "sk-agent-abc123" in captured.out

    def test_add_agent_with_display_name(self, temp_store, keys_file, capsys):
        """Test adding agent with display name."""
        args = self._make_args(
            temp_store, "add", agent_id="my_agent", name="My Cool Agent"
        )

        with patch("secrets.token_hex", return_value="def456"):
            result = _agents_command(args)

        assert result == 0

        with open(keys_file) as f:
            data = yaml.safe_load(f)

        api_key = "sk-agent-def456"
        assert data["agents"][api_key]["agent_id"] == "my_agent"
        assert data["agents"][api_key]["display_name"] == "My Cool Agent"

        captured = capsys.readouterr()
        assert "Display name: My Cool Agent" in captured.out

    def test_add_duplicate_agent(self, temp_store):
        """Test adding duplicate agent fails."""
        # First add
        args = self._make_args(temp_store, "add", agent_id="dup_agent")
        with patch("secrets.token_hex", return_value="first123"):
            _agents_command(args)

        # Second add with same agent_id
        args = self._make_args(temp_store, "add", agent_id="dup_agent")
        with patch("secrets.token_hex", return_value="second456"):
            result = _agents_command(args)

        assert result == 1

    def test_list_empty(self, temp_store, capsys):
        """Test listing agents when none exist."""
        args = self._make_args(temp_store, "list")

        result = _agents_command(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "No agents registered" in captured.out

    def test_list_agents(self, temp_store, keys_file, capsys):
        """Test listing agents."""
        # Create some agents
        data = {
            "agents": {
                "sk-agent-key1": {"agent_id": "agent_alpha", "display_name": "Alpha"},
                "sk-agent-key2": {"agent_id": "agent_beta"},
            }
        }
        with open(keys_file, "w") as f:
            yaml.safe_dump(data, f)

        args = self._make_args(temp_store, "list")
        result = _agents_command(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "agent_alpha" in captured.out
        assert "Alpha" in captured.out
        assert "agent_beta" in captured.out

    def test_list_agents_json(self, temp_store, keys_file, capsys):
        """Test listing agents with JSON output."""
        import json

        data = {
            "agents": {
                "sk-agent-key1": {"agent_id": "agent_alpha", "display_name": "Alpha"},
            }
        }
        with open(keys_file, "w") as f:
            yaml.safe_dump(data, f)

        args = self._make_args(temp_store, "list", json_output=True)
        result = _agents_command(args)

        assert result == 0
        captured = capsys.readouterr()

        # Parse JSON output
        output = json.loads(captured.out)
        assert len(output) == 1
        assert output[0]["agentId"] == "agent_alpha"
        assert output[0]["displayName"] == "Alpha"
        assert "keyPrefix" in output[0]
        # Key prefix should be masked
        assert output[0]["keyPrefix"].startswith("sk-agent-key")
        assert "..." in output[0]["keyPrefix"]

    def test_remove_agent(self, temp_store, keys_file, capsys):
        """Test removing an agent."""
        # Create an agent
        data = {
            "agents": {
                "sk-agent-remove": {"agent_id": "to_remove"},
            }
        }
        with open(keys_file, "w") as f:
            yaml.safe_dump(data, f)

        # Remove with --yes flag (skip confirmation)
        args = self._make_args(temp_store, "remove", agent_id="to_remove", yes=True)
        result = _agents_command(args)

        assert result == 0

        # Verify agent was removed
        with open(keys_file) as f:
            data = yaml.safe_load(f)

        assert len(data["agents"]) == 0

        captured = capsys.readouterr()
        assert "Removed agent 'to_remove'" in captured.out

    def test_remove_nonexistent_agent(self, temp_store, keys_file):
        """Test removing nonexistent agent fails."""
        # Create empty keys file
        data = {"agents": {}}
        with open(keys_file, "w") as f:
            yaml.safe_dump(data, f)

        args = self._make_args(temp_store, "remove", agent_id="nonexistent", yes=True)
        result = _agents_command(args)

        assert result == 1

    def test_remove_agent_cancel(self, temp_store, keys_file, capsys):
        """Test cancelling agent removal."""
        # Create an agent
        data = {
            "agents": {
                "sk-agent-keep": {"agent_id": "keep_me"},
            }
        }
        with open(keys_file, "w") as f:
            yaml.safe_dump(data, f)

        # Remove without --yes, simulate user typing 'n'
        args = self._make_args(temp_store, "remove", agent_id="keep_me", yes=False)

        with patch("builtins.input", return_value="n"):
            result = _agents_command(args)

        assert result == 0

        # Agent should still exist
        with open(keys_file) as f:
            data = yaml.safe_load(f)

        assert "sk-agent-keep" in data["agents"]

        captured = capsys.readouterr()
        assert "Cancelled" in captured.out

    def test_remove_agent_confirm(self, temp_store, keys_file):
        """Test confirming agent removal."""
        # Create an agent
        data = {
            "agents": {
                "sk-agent-confirm": {"agent_id": "confirm_remove"},
            }
        }
        with open(keys_file, "w") as f:
            yaml.safe_dump(data, f)

        # Remove without --yes, simulate user typing 'y'
        args = self._make_args(
            temp_store, "remove", agent_id="confirm_remove", yes=False
        )

        with patch("builtins.input", return_value="y"):
            result = _agents_command(args)

        assert result == 0

        # Agent should be removed
        with open(keys_file) as f:
            data = yaml.safe_load(f)

        assert len(data["agents"]) == 0

    def test_add_creates_store_directory(self, tmp_path):
        """Test add command creates store directory if it doesn't exist."""
        store_dir = tmp_path / "new_store"
        assert not store_dir.exists()

        args = self._make_args(store_dir, "add", agent_id="first_agent")

        with patch("secrets.token_hex", return_value="newkey"):
            result = _agents_command(args)

        assert result == 0
        assert store_dir.exists()
        assert (store_dir / "agent_keys.yaml").exists()

    def test_list_simple_format_agents(self, temp_store, keys_file, capsys):
        """Test listing agents with simple string format."""
        # Use simple string format (just agent_id as value)
        data = {
            "agents": {
                "sk-agent-simple": "simple_agent_id",
            }
        }
        with open(keys_file, "w") as f:
            yaml.safe_dump(data, f)

        args = self._make_args(temp_store, "list")
        result = _agents_command(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "simple_agent_id" in captured.out
