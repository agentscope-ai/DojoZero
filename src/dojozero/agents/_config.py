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
    """Agent YAML configuration structure.

    The llm field is a list of LLMConfig dicts. Each model in the list
    will create a separate agent instance with that model.
    """

    name: str
    sys_prompt: str
    tools: list[str]  # List of tool names to enable
    llm: list[LLMConfig]  # List of models - each creates a separate agent


class SingleModelAgentConfig(TypedDict):
    """Agent configuration with a single model (used after expansion)."""

    name: str
    sys_prompt: str
    tools: list[str]
    llm: LLMConfig  # Single model config


def _parse_llm_config(llm_data: dict[str, Any]) -> LLMConfig:
    """Parse a single LLM config dict into LLMConfig."""
    llm_config: LLMConfig = {
        "model_type": llm_data.get("model_type", "openai"),
        "model_name": llm_data.get("model_name", "qwen3-max"),
    }
    if "api_key_env" in llm_data:
        llm_config["api_key_env"] = llm_data["api_key_env"]
    if "base_url_env" in llm_data:
        llm_config["base_url_env"] = llm_data["base_url_env"]
    return llm_config


def load_agent_config(config_path: str | Path) -> AgentConfig:
    """Load agent configuration from YAML file.

    Args:
        config_path: Path to YAML config file

    Returns:
        Parsed AgentConfig dictionary with llm as a list of LLMConfig
    """
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)

    # Parse LLM config - always as a list
    llm_data = data.get("llm", [])
    llm_configs: list[LLMConfig] = []

    if isinstance(llm_data, list):
        # Already a list of model configs
        for item in llm_data:
            llm_configs.append(_parse_llm_config(item))
    else:
        # Single model config (backwards compatibility during transition)
        llm_configs.append(_parse_llm_config(llm_data))

    # Build AgentConfig with all required fields
    return AgentConfig(
        name=data.get("name", ""),
        sys_prompt=data.get("sys_prompt", ""),
        tools=data.get("tools", []),
        llm=llm_configs,
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


def expand_agent_config(config: AgentConfig) -> list[SingleModelAgentConfig]:
    """Expand an AgentConfig with multiple models into separate configs.

    Each model in the llm list creates a separate SingleModelAgentConfig.
    The agent name is suffixed with the model name for uniqueness.

    Args:
        config: AgentConfig with potentially multiple models

    Returns:
        List of SingleModelAgentConfig, one per model
    """
    expanded: list[SingleModelAgentConfig] = []
    base_name = config["name"]

    for llm_config in config["llm"]:
        model_name = llm_config.get("model_name", "unknown")
        # Create unique name by appending model name
        agent_name = f"{base_name}-{model_name}"

        expanded.append(
            SingleModelAgentConfig(
                name=agent_name,
                sys_prompt=config["sys_prompt"],
                tools=config["tools"],
                llm=llm_config,
            )
        )

    return expanded
