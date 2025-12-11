"""YAML-based agent configuration loader."""

import os
from pathlib import Path
from typing import Any, Literal, TypedDict

import yaml

ModelType = Literal["openai", "dashscope"]


class LLMConfig(TypedDict, total=False):
    """LLM configuration."""

    model_type: str  # "openai" or "dashscope"
    model_name: str
    api_key_env: str
    base_url_env: str


class AgentConfig(TypedDict):
    """Agent YAML configuration structure."""

    agent_id: str
    name: str
    sys_prompt: str
    tools: list[str]  # List of tool names to enable
    llm: LLMConfig


def load_agent_config(config_path: str | Path) -> AgentConfig:
    """Load agent configuration from YAML file.

    Args:
        config_path: Path to YAML config file

    Returns:
        Parsed AgentConfig dictionary
    """
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)

    config: AgentConfig = {
        "agent_id": data["agent_id"],
        "name": data.get("name", data["agent_id"]),
        "sys_prompt": data.get("sys_prompt", ""),
        "tools": data.get("tools", []),
    }

    # Parse LLM config
    llm_data = data.get("llm", {})
    llm_config: LLMConfig = {
        "model_type": llm_data.get("model_type", "openai"),
        "model_name": llm_data.get("model_name", "qwen3-max"),
    }
    if "api_key_env" in llm_data:
        llm_config["api_key_env"] = llm_data["api_key_env"]
    if "base_url_env" in llm_data:
        llm_config["base_url_env"] = llm_data["base_url_env"]

    config["llm"] = llm_config

    return config


def get_api_key(llm_config: LLMConfig) -> str:
    """Get API key from environment variable."""
    env_var = llm_config.get("api_key_env", "OPENAI_API_KEY")
    return os.environ.get(env_var, "")


def get_base_url(llm_config: LLMConfig) -> str | None:
    """Get base URL from environment variable."""
    env_var = llm_config.get("base_url_env", "OPENAI_BASE_URL")
    return os.environ.get(env_var)


class BettingAgentConfig(TypedDict, total=False):
    actor_id: str
    name: str
    sys_prompt: str
    model_type: ModelType
    model_name: str
    api_key_env: str  # Environment variable name for API key
    base_url_env: str  # Environment variable name for base URL (optional)
    agent_config_path: str  # Path to agent YAML config file (alternative to inline config)
