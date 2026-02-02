"""Agent configuration and model creation."""

import os
from pathlib import Path
from typing import Any, Literal, TypedDict

import yaml
from agentscope.formatter import (
    FormatterBase,
    OpenAIChatFormatter,
    DashScopeChatFormatter,
    AnthropicChatFormatter,
    GeminiChatFormatter,
)
from agentscope.model import (
    ChatModelBase,
    OpenAIChatModel,
    DashScopeChatModel,
    AnthropicChatModel,
    GeminiChatModel,
)

ModelType = Literal["openai", "dashscope", "anthropic", "gemini"]


class LLMConfig(TypedDict, total=False):
    """LLM configuration."""

    model_type: ModelType  # "openai", "dashscope", "anthropic", or "gemini"
    model_name: str
    api_key_env: str
    base_url_env: str
    max_tokens: int  # Max tokens for response generation (default: 16384)


class PersonaConfig(TypedDict):
    """Persona configuration - contains only sys_prompt."""

    sys_prompt: str


class LLMFileConfig(TypedDict):
    """LLM file configuration - contains list of LLM configs."""

    llm: list[LLMConfig]


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
    if "max_tokens" in llm_data:
        llm_config["max_tokens"] = llm_data["max_tokens"]
    return llm_config


def load_persona_config(config_path: str | Path) -> PersonaConfig:
    """Load persona configuration from YAML file.

    Args:
        config_path: Path to persona YAML config file (contains only sys_prompt)

    Returns:
        Parsed PersonaConfig dictionary
    """
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)

    return PersonaConfig(sys_prompt=data.get("sys_prompt", ""))


def load_llm_file_config(config_path: str | Path) -> LLMFileConfig:
    """Load LLM configuration from YAML file.

    Args:
        config_path: Path to LLM YAML config file (contains llm list)

    Returns:
        Parsed LLMFileConfig dictionary with llm as a list of LLMConfig
    """
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)

    llm_data = data.get("llm", [])
    llm_configs: list[LLMConfig] = []

    if isinstance(llm_data, list):
        for item in llm_data:
            llm_configs.append(_parse_llm_config(item))
    else:
        # Single model config (backwards compatibility)
        llm_configs.append(_parse_llm_config(llm_data))

    return LLMFileConfig(llm=llm_configs)


def load_agent_config(
    persona_config_path: str | Path,
    llm_config_path: str | Path,
    name: str = "",
) -> AgentConfig:
    """Load agent configuration from separate persona and LLM config files.

    Args:
        persona_config_path: Path to persona YAML config (contains sys_prompt)
        llm_config_path: Path to LLM YAML config (contains llm list)
        name: Optional agent name

    Returns:
        Parsed AgentConfig combining persona and LLM configs

    Raises:
        ValueError: If multiple models are specified with duplicate names
    """
    persona_config = load_persona_config(persona_config_path)
    llm_file_config = load_llm_file_config(llm_config_path)

    # Validate unique model names when multiple models are specified
    llm_configs = llm_file_config["llm"]
    if len(llm_configs) > 1:
        model_names = [c.get("model_name", "qwen3-max") for c in llm_configs]
        if len(model_names) != len(set(model_names)):
            raise ValueError(
                f"Duplicate model names found in LLM config '{llm_config_path}'. "
                "Model names must be unique when multiple models are specified."
            )

    return AgentConfig(
        name=name,
        sys_prompt=persona_config["sys_prompt"],
        tools=[],  # Tools are specified in trial params, not in config files
        llm=llm_configs,
    )


def get_api_key(llm_config: LLMConfig | dict[str, Any]) -> str:
    """Get API key from environment variable."""
    env_var = llm_config.get("api_key_env", "DOJOZERO_OPENAI_API_KEY")
    return os.environ.get(env_var, "")


def create_model(llm_config: LLMConfig) -> ChatModelBase:
    """Create model from LLM config dict."""
    model_type = llm_config.get("model_type", "openai")
    model_name = llm_config.get("model_name", "qwen3-max")
    api_key = get_api_key(llm_config)
    if model_type == "openai":
        return OpenAIChatModel(model_name=model_name, api_key=api_key)
    elif model_type == "dashscope":
        return DashScopeChatModel(model_name=model_name, api_key=api_key)
    elif model_type == "anthropic":
        return AnthropicChatModel(model_name=model_name, api_key=api_key)
    elif model_type == "gemini":
        return GeminiChatModel(model_name=model_name, api_key=api_key)
    else:
        raise ValueError(f"Unknown model_type: {model_type}")


def create_formatter(model_type: str) -> FormatterBase:
    """Create formatter matching model type."""
    if model_type == "openai":
        return OpenAIChatFormatter()
    elif model_type == "dashscope":
        return DashScopeChatFormatter()
    elif model_type == "anthropic":
        return AnthropicChatFormatter()
    elif model_type == "gemini":
        return GeminiChatFormatter()
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
