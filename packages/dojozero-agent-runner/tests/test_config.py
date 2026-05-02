"""Tests for ``RunnerConfig`` env loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from dojozero_agent_runner._config import load_config_from_env


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def persona_yaml(tmp_path: Path) -> Path:
    """Persona YAML with a sys_prompt — mimics the real ``degen.yaml`` shape."""
    p = tmp_path / "personas" / "degen.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        'sys_prompt: |\n  You are "Danny Hype." Keep it short.\n',
        encoding="utf-8",
    )
    return p


@pytest.fixture
def llm_yaml(tmp_path: Path) -> Path:
    """LLM matrix YAML with two entries — Claude and Qwen."""
    p = tmp_path / "llms" / "default.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        """\
llm:
  - model_type: anthropic
    model_name: claude-haiku-4-5-20251001
    api_key_env: DOJOZERO_ANTHROPIC_API_KEY
    model_display_name: Claude
  - model_type: dashscope
    model_name: qwen3-max
    api_key_env: DOJOZERO_DASHSCOPE_API_KEY
    model_display_name: Qwen
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def base_env(monkeypatch, persona_yaml, llm_yaml):
    """Set all required env vars (DojoZero + AgentID) for a successful load."""
    monkeypatch.setenv("DOJOZERO_PERSONA", "degen")
    monkeypatch.setenv("DOJOZERO_LLM", "Claude")
    monkeypatch.setenv("DOJOZERO_TRIAL_ID", "trial-canary-001")
    monkeypatch.setenv("DOJOZERO_GATEWAY_URL", "https://api.dojozero.live")
    monkeypatch.setenv("DOJOZERO_PERSONA_PATH", str(persona_yaml))
    monkeypatch.setenv("DOJOZERO_LLM_CONFIG_PATH", str(llm_yaml))
    monkeypatch.setenv("AGENTID_AGENT_ID", "agentid:dojozero:degen-claude-canary")
    monkeypatch.setenv("AGENTID_AGENT_KID", "0123456789abcdef")
    monkeypatch.setenv("AGENTID_PRIVATE_KEY", "00" * 32)
    monkeypatch.delenv("DOJOZERO_AGENTID_AUDIENCE", raising=False)
    monkeypatch.delenv("DOJOZERO_POLL_INTERVAL_SECONDS", raising=False)


# ---------------------------------------------------------------------------
# Required-field validation
# ---------------------------------------------------------------------------


class TestRequiredFields:
    @pytest.mark.parametrize(
        "missing",
        [
            "DOJOZERO_PERSONA",
            "DOJOZERO_LLM",
            "DOJOZERO_TRIAL_ID",
            "DOJOZERO_GATEWAY_URL",
            "AGENTID_AGENT_ID",
            "AGENTID_AGENT_KID",
            "AGENTID_PRIVATE_KEY",
        ],
    )
    def test_missing_required_env_raises(self, monkeypatch, base_env, missing):
        monkeypatch.delenv(missing, raising=False)
        with pytest.raises(ValueError, match=f"Required env var not set: {missing}"):
            load_config_from_env()


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


class TestYamlLoading:
    def test_loads_persona_sys_prompt(self, base_env):
        cfg = load_config_from_env()
        assert "Danny Hype" in cfg.sys_prompt

    def test_resolves_llm_by_display_name_case_insensitive(self, monkeypatch, base_env):
        monkeypatch.setenv("DOJOZERO_LLM", "claude")  # lowercase
        cfg = load_config_from_env()
        assert cfg.llm_config["model_name"] == "claude-haiku-4-5-20251001"
        assert cfg.llm_config["model_type"] == "anthropic"

    def test_resolves_llm_by_model_name(self, monkeypatch, base_env):
        monkeypatch.setenv("DOJOZERO_LLM", "qwen3-max")
        cfg = load_config_from_env()
        assert cfg.llm_config["model_display_name"] == "Qwen"

    def test_unknown_llm_raises_with_available_list(self, monkeypatch, base_env):
        monkeypatch.setenv("DOJOZERO_LLM", "GPT-9")
        with pytest.raises(ValueError, match="Available:.*Claude.*Qwen"):
            load_config_from_env()

    def test_missing_persona_yaml_raises(self, monkeypatch, base_env, tmp_path):
        monkeypatch.setenv(
            "DOJOZERO_PERSONA_PATH", str(tmp_path / "does-not-exist.yaml")
        )
        with pytest.raises(ValueError, match="Persona YAML not found"):
            load_config_from_env()

    def test_persona_without_sys_prompt_raises(self, monkeypatch, base_env, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("not_sys_prompt: foo\n", encoding="utf-8")
        monkeypatch.setenv("DOJOZERO_PERSONA_PATH", str(bad))
        with pytest.raises(ValueError, match="missing a non-empty 'sys_prompt'"):
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
