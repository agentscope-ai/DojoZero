"""Agent configuration and model creation."""

import os
from pathlib import Path
from typing import Any, Literal, TypedDict

import yaml
from agentscope.formatter import (
    FormatterBase,
    OpenAIChatFormatter,
    DashScopeChatFormatter,
)
from agentscope.model import ChatModelBase, OpenAIChatModel, DashScopeChatModel

ModelType = Literal["openai", "dashscope"]


class LLMConfig(TypedDict, total=False):
    """LLM configuration."""

    model_type: ModelType  # "openai" or "dashscope"
    model_name: str
    api_key_env: str
    base_url_env: str


class AgentConfig(TypedDict):
    """Agent YAML configuration structure."""

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

    # Parse LLM config first
    llm_data = data.get("llm", {})
    llm_config: LLMConfig = {
        "model_type": llm_data.get("model_type", "openai"),
        "model_name": llm_data.get("model_name", "qwen3-max"),
    }
    if "api_key_env" in llm_data:
        llm_config["api_key_env"] = llm_data["api_key_env"]
    if "base_url_env" in llm_data:
        llm_config["base_url_env"] = llm_data["base_url_env"]

    # Build AgentConfig with all required fields
    return AgentConfig(
        name=data.get("name", ""),
        sys_prompt=data.get("sys_prompt", ""),
        tools=data.get("tools", []),
        llm=llm_config,
    )


def get_api_key(llm_config: LLMConfig | dict[str, Any]) -> str:
    """Get API key from environment variable."""
    env_var = llm_config.get("api_key_env", "DOJOZERO_OPENAI_API_KEY")
    return os.environ.get(env_var, "")


def get_base_url(llm_config: LLMConfig | dict[str, Any]) -> str | None:
    """Get base URL from environment variable."""
    env_var = llm_config.get("base_url_env", "DOJOZERO_OPENAI_BASE_URL")
    return os.environ.get(env_var)


def create_model(llm_config: LLMConfig) -> ChatModelBase:
    """Create model from LLM config dict."""
    model_type = llm_config.get("model_type", "openai")
    model_name = llm_config.get("model_name", "qwen3-max")
    api_key = get_api_key(llm_config)
    base_url = get_base_url(llm_config)

    if model_type == "openai":
        client_args = {"base_url": base_url} if base_url else None
        return OpenAIChatModel(
            model_name=model_name, api_key=api_key, client_args=client_args
        )
    elif model_type == "dashscope":
        return DashScopeChatModel(model_name=model_name, api_key=api_key)
    else:
        raise ValueError(f"Unknown model_type: {model_type}")


def create_formatter(model_type: str) -> FormatterBase:
    """Create formatter matching model type."""
    if model_type == "openai":
        return OpenAIChatFormatter()
    elif model_type == "dashscope":
        return DashScopeChatFormatter()
    else:
        raise ValueError(f"Unknown model_type: {model_type}")
