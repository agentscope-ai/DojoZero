"""Tests for ``RunnerConfig`` env loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from dojozero_agent_runner._config import load_config_from_env


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sys_prompt_file(tmp_path: Path) -> Path:
    """Plain-text system prompt — the runner accepts these as raw text."""
    p = tmp_path / "prompts" / "danny.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        'You are "Danny Hype." Keep it short.\n',
        encoding="utf-8",
    )
    return p


@pytest.fixture
def model_config_yaml(tmp_path: Path) -> Path:
    """Single-model YAML matching the runner's expected llm_config shape."""
    p = tmp_path / "models" / "claude.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        """\
model_type: anthropic
model_name: claude-haiku-4-5-20251001
api_key_env: DOJOZERO_ANTHROPIC_API_KEY
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def agent_brain_yaml(tmp_path: Path) -> Path:
    """Single-file agent brain: sys_prompt + model: in one YAML."""
    p = tmp_path / "agent-brains" / "dojozero-degen-claude.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        """\
sys_prompt: |
  You are "Config Persona."
model:
  type: anthropic
  name: claude-haiku-4-5-20251001
  api_key_env: ANTHROPIC_API_KEY
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def base_env(monkeypatch, sys_prompt_file, model_config_yaml):
    """Set required env vars for a successful load via piecewise brain inputs."""
    monkeypatch.setenv("DOJOZERO_TRIAL_ID", "trial-canary-001")
    monkeypatch.setenv("DOJOZERO_GATEWAY_URL", "https://api.dojozero.live")
    monkeypatch.setenv("DOJOZERO_AGENT_PROMPT", str(sys_prompt_file))
    monkeypatch.setenv("DOJOZERO_AGENT_MODEL", str(model_config_yaml))
    monkeypatch.setenv("AGENTID_AGENT_ID", "agentid:dojozero:degen-claude-canary")
    monkeypatch.setenv("AGENTID_AGENT_KID", "0123456789abcdef")
    monkeypatch.setenv("AGENTID_PRIVATE_KEY", "00" * 32)
    monkeypatch.delenv("DOJOZERO_AGENT_PROFILE", raising=False)
    monkeypatch.delenv("DOJOZERO_AGENT_BRAIN", raising=False)
    monkeypatch.delenv("DOJOZERO_AGENTID_AUDIENCE", raising=False)
    monkeypatch.delenv("DOJOZERO_POLL_INTERVAL_SECONDS", raising=False)


# ---------------------------------------------------------------------------
# Required-field validation
# ---------------------------------------------------------------------------


class TestRequiredFields:
    @pytest.mark.parametrize(
        "missing",
        [
            "DOJOZERO_TRIAL_ID",
            "AGENTID_AGENT_ID",
            "AGENTID_AGENT_KID",
            "AGENTID_PRIVATE_KEY",
        ],
    )
    def test_missing_required_env_raises(self, monkeypatch, base_env, missing):
        monkeypatch.delenv(missing, raising=False)
        with pytest.raises(ValueError, match=f"Required env var not set: {missing}"):
            load_config_from_env()

    def test_missing_both_url_envs_raises(self, monkeypatch, base_env):
        monkeypatch.delenv("DOJOZERO_GATEWAY_URL", raising=False)
        monkeypatch.delenv("DOJOZERO_DASHBOARD_URL", raising=False)
        with pytest.raises(
            ValueError,
            match="DOJOZERO_DASHBOARD_URL or DOJOZERO_GATEWAY_URL is required",
        ):
            load_config_from_env()

    def test_dashboard_url_derives_gateway_url(self, monkeypatch, base_env):
        monkeypatch.delenv("DOJOZERO_GATEWAY_URL", raising=False)
        monkeypatch.setenv("DOJOZERO_DASHBOARD_URL", "http://localhost:8080")
        cfg = load_config_from_env()
        assert cfg.gateway_url == (f"http://localhost:8080/api/trials/{cfg.trial_id}")

    def test_missing_brain_raises(self, monkeypatch, base_env):
        # No agent-brain, no piecewise files → can't resolve sys_prompt.
        monkeypatch.delenv("DOJOZERO_AGENT_PROMPT", raising=False)
        monkeypatch.delenv("DOJOZERO_AGENT_MODEL", raising=False)
        with pytest.raises(ValueError, match="no sys_prompt resolved"):
            load_config_from_env()


# ---------------------------------------------------------------------------
# Brain loading — piecewise (--agent-prompt + --agent-model)
# ---------------------------------------------------------------------------


class TestPiecewiseBrainLoading:
    def test_loads_sys_prompt_from_file(self, base_env):
        cfg = load_config_from_env()
        assert "Danny Hype" in cfg.sys_prompt

    def test_loads_model_config_from_file(self, base_env):
        cfg = load_config_from_env()
        assert cfg.llm_config["model_type"] == "anthropic"
        assert cfg.llm_config["model_name"] == "claude-haiku-4-5-20251001"

    def test_missing_sys_prompt_file_raises(self, monkeypatch, base_env, tmp_path):
        monkeypatch.setenv("DOJOZERO_AGENT_PROMPT", str(tmp_path / "does-not-exist.md"))
        with pytest.raises(ValueError, match="agent prompt file not found"):
            load_config_from_env()

    def test_missing_model_config_raises(self, monkeypatch, base_env, tmp_path):
        monkeypatch.setenv(
            "DOJOZERO_AGENT_MODEL", str(tmp_path / "does-not-exist.yaml")
        )
        with pytest.raises(ValueError, match="agent model file not found"):
            load_config_from_env()

    def test_empty_model_config_raises(self, monkeypatch, base_env, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("# nothing here\n", encoding="utf-8")
        monkeypatch.setenv("DOJOZERO_AGENT_MODEL", str(empty))
        with pytest.raises(ValueError, match="non-empty YAML mapping"):
            load_config_from_env()


# ---------------------------------------------------------------------------
# Brain loading — single agent-brain YAML
# ---------------------------------------------------------------------------


class TestAgentBrainLoading:
    def test_agent_config_supplies_brain(self, monkeypatch, base_env, agent_brain_yaml):
        # Clear piecewise inputs, use agent-brain alone.
        monkeypatch.delenv("DOJOZERO_AGENT_PROMPT", raising=False)
        monkeypatch.delenv("DOJOZERO_AGENT_MODEL", raising=False)
        monkeypatch.setenv("DOJOZERO_AGENT_BRAIN", str(agent_brain_yaml))
        cfg = load_config_from_env()
        assert "Config Persona" in cfg.sys_prompt
        assert cfg.llm_config["model_type"] == "anthropic"
        assert cfg.llm_config["model_name"] == "claude-haiku-4-5-20251001"
        # Display name derived from the config filename
        assert cfg.agent_display_name == "dojozero-degen-claude"

    def test_piecewise_overrides_agent_config_sys_prompt(
        self, monkeypatch, base_env, agent_brain_yaml
    ):
        # base_env sets DOJOZERO_AGENT_PROMPT; that should win over agent-brain.
        monkeypatch.setenv("DOJOZERO_AGENT_BRAIN", str(agent_brain_yaml))
        cfg = load_config_from_env()
        assert "Danny Hype" in cfg.sys_prompt
        assert "Config Persona" not in cfg.sys_prompt

    def test_agent_config_missing_sys_prompt_raises(
        self, monkeypatch, base_env, tmp_path
    ):
        bad = tmp_path / "bad.yaml"
        bad.write_text("model:\n  type: x\n  name: y\n", encoding="utf-8")
        monkeypatch.delenv("DOJOZERO_AGENT_PROMPT", raising=False)
        monkeypatch.delenv("DOJOZERO_AGENT_MODEL", raising=False)
        monkeypatch.setenv("DOJOZERO_AGENT_BRAIN", str(bad))
        with pytest.raises(ValueError, match="missing a non-empty 'sys_prompt'"):
            load_config_from_env()


# ---------------------------------------------------------------------------
# Profile dir is identity-only (does NOT supply the brain)
# ---------------------------------------------------------------------------


class TestProfileIsIdentityOnly:
    @pytest.fixture
    def profile_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "agents" / "test-agent"
        d.mkdir(parents=True)
        (d / "agent.json").write_text(
            '{"agent_id": "agentid:test:agent_x", "kid": "0011"}',
            encoding="utf-8",
        )
        (d / "private_key").write_bytes(b"\x00" * 32)
        # NOTE: deliberately no agent.yaml — the profile is identity-only.
        return d

    def test_profile_with_piecewise_brain(self, monkeypatch, base_env, profile_dir):
        monkeypatch.setenv("DOJOZERO_AGENT_PROFILE", str(profile_dir))
        cfg = load_config_from_env()
        # Brain came from base_env piecewise inputs, not the profile.
        assert "Danny Hype" in cfg.sys_prompt
        # Display name falls back to sys_prompt file stem when no agent-brain.
        assert cfg.agent_display_name == "danny"

    def test_profile_without_brain_raises(self, monkeypatch, base_env, profile_dir):
        # Profile alone is NOT enough — brain must be supplied separately.
        monkeypatch.delenv("DOJOZERO_AGENT_PROMPT", raising=False)
        monkeypatch.delenv("DOJOZERO_AGENT_MODEL", raising=False)
        monkeypatch.setenv("DOJOZERO_AGENT_PROFILE", str(profile_dir))
        with pytest.raises(ValueError, match="no sys_prompt resolved"):
            load_config_from_env()


# ---------------------------------------------------------------------------
# Optional fields & defaults
# ---------------------------------------------------------------------------


class TestOptionalFields:
    def test_poll_interval_default(self, base_env):
        cfg = load_config_from_env()
        assert cfg.poll_interval_seconds == 5.0

    def test_poll_interval_override(self, monkeypatch, base_env):
        monkeypatch.setenv("DOJOZERO_POLL_INTERVAL_SECONDS", "1.5")
        cfg = load_config_from_env()
        assert cfg.poll_interval_seconds == 1.5

    def test_poll_interval_invalid_raises(self, monkeypatch, base_env):
        monkeypatch.setenv("DOJOZERO_POLL_INTERVAL_SECONDS", "soon")
        with pytest.raises(ValueError, match="must be a number"):
            load_config_from_env()

    def test_agentid_audience_defaults_to_none(self, base_env):
        """None means 'use gateway_url'; runner code interprets that."""
        cfg = load_config_from_env()
        assert cfg.agentid_audience is None

    def test_agentid_audience_override(self, monkeypatch, base_env):
        monkeypatch.setenv("DOJOZERO_AGENTID_AUDIENCE", "https://custom-aud.test")
        cfg = load_config_from_env()
        assert cfg.agentid_audience == "https://custom-aud.test"

    def test_gateway_url_trailing_slash_stripped(self, monkeypatch, base_env):
        monkeypatch.setenv("DOJOZERO_GATEWAY_URL", "https://api.dojozero.live/")
        cfg = load_config_from_env()
        assert cfg.gateway_url == "https://api.dojozero.live"
