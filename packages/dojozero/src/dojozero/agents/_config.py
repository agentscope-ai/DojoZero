"""Agent configuration and model creation."""

import logging
import os
from pathlib import Path
from typing import Any, Literal, TypedDict

import yaml
from pydantic import BaseModel
from agentscope.formatter import (
    FormatterBase,
    OpenAIChatFormatter,
    DashScopeChatFormatter,
    AnthropicChatFormatter,
    GeminiChatFormatter,
)
from agentscope.message import Msg
from agentscope.model import (
    ChatModelBase,
    OpenAIChatModel,
    DashScopeChatModel,
    AnthropicChatModel,
    GeminiChatModel,
)

LOGGER = logging.getLogger(__name__)

ModelType = Literal["openai", "dashscope", "anthropic", "gemini", "grok"]


class GrokChatFormatter(OpenAIChatFormatter):
    """Grok-specific formatter that only includes 'name' field for user messages.

    Grok's API (via xAI) only supports the 'name' field on messages with role='user'.
    This formatter extends OpenAIChatFormatter but removes the 'name' field from
    non-user messages to avoid API errors.
    """

    async def _format(self, msgs: list[Msg]) -> list[dict[str, Any]]:
        """Format messages, removing 'name' from non-user messages."""
        formatted = await super()._format(msgs)

        for msg in formatted:
            if msg.get("role") != "user" and "name" in msg:
                del msg["name"]

        return formatted


# Pydantic model for YAML parsing/validation only
class _LLMConfigModel(BaseModel):
    """Internal Pydantic model for parsing LLM config from YAML."""

    model_type: ModelType = "openai"
    model_name: str
    api_key_env: str
    base_url_env: str | None = None
    model_display_name: str | None = None
    cdn_url: str | None = None


# TypedDict for use throughout the codebase
class LLMConfig(TypedDict, total=False):
    """LLM configuration."""

    model_type: ModelType  # "openai", "dashscope", "anthropic", or "gemini"
    model_name: str
    api_key_env: str
    base_url_env: str | None
    model_display_name: str | None  # Human-readable model name (e.g., "qwen", "claude")
    cdn_url: str | None  # Avatar image URL for the model


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


def _parse_llm_config(data: dict[str, Any]) -> LLMConfig:
    """Parse and validate LLM config using Pydantic, return as TypedDict."""
    validated = _LLMConfigModel.model_validate(data)
    # Convert to TypedDict (dict) - exclude_none to keep it clean
    return LLMConfig(**validated.model_dump(exclude_none=True))


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

    Supports two formats:

    1. Inline models::

        llm:
          - model_type: dashscope
            model_name: qwen3-max
            ...

    2. Include references to other LLM config files::

        include:
          - qwen.yaml
          - claude.yaml

    Include paths are resolved relative to the config file's directory.
    Both ``include`` and ``llm`` can coexist; included models come first.

    Args:
        config_path: Path to LLM YAML config file

    Returns:
        Parsed LLMFileConfig dictionary with llm as a list of LLMConfig
    """
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)

    llm_configs: list[LLMConfig] = []

    # Resolve included files first
    includes = data.get("include", [])
    if isinstance(includes, list):
        for ref in includes:
            ref_path = path.parent / ref
            included = load_llm_file_config(ref_path)
            llm_configs.extend(included["llm"])

    # Then add inline models
    llm_data = data.get("llm", [])
    if isinstance(llm_data, list):
        for item in llm_data:
            llm_configs.append(_parse_llm_config(item))
    elif llm_data:
        # Single model config (backwards compatibility)
        llm_configs.append(_parse_llm_config(llm_data))

    return LLMFileConfig(llm=llm_configs)


def llm_config_has_credentials(llm_config: LLMConfig) -> bool:
    """True if required API key (and optional base URL) env vars are set and non-empty."""
    api_key_env = llm_config.get("api_key_env", "")
    if not api_key_env:
        return False
    key = os.environ.get(api_key_env)
    if not key or not str(key).strip():
        return False
    base_url_env = llm_config.get("base_url_env")
    if base_url_env:
        base_url = os.environ.get(base_url_env)
        if not base_url or not str(base_url).strip():
            return False
    return True


def filter_llm_configs_by_credentials(llm_configs: list[LLMConfig]) -> list[LLMConfig]:
    """Keep only LLM entries whose credential env vars are present."""
    usable: list[LLMConfig] = []
    for c in llm_configs:
        if llm_config_has_credentials(c):
            usable.append(c)
        else:
            env = c.get("api_key_env") or ""
            mn = c.get("model_name", "?")
            LOGGER.info(
                "Skipping LLM %r: missing or empty environment variable %s",
                mn,
                repr(env) if env else "api_key_env",
            )
    return usable


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

    # Only enable models whose API keys (and optional base URL) are configured
    llm_configs = filter_llm_configs_by_credentials(llm_file_config["llm"])

    # Validate unique model names when multiple models are specified
    if len(llm_configs) > 1:
        model_names = [c.get("model_name", "") for c in llm_configs]
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


def create_model(llm_config: LLMConfig) -> ChatModelBase:
    """Create model from LLM config."""
    model_type = llm_config.get("model_type", "openai")
    model_name = llm_config.get("model_name", "")
    api_key_env = llm_config.get("api_key_env", "")
    if not api_key_env:
        raise ValueError("Missing 'api_key_env' in LLM config")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(f"API key environment variable '{api_key_env}' is not set.")
    base_url_env = llm_config.get("base_url_env")
    if base_url_env:
        base_url = os.environ.get(base_url_env)
        if not base_url:
            raise ValueError(
                f"Base URL environment variable '{base_url_env}' is not set."
            )
    else:
        base_url = None

    if model_type in "grok":
        grok_client_kwargs: dict[str, Any] = {"base_url": "https://api.x.ai/v1"}
        return OpenAIChatModel(
            model_name=model_name, api_key=api_key, client_kwargs=grok_client_kwargs
        )
    elif model_type == "openai":
        if base_url:
            client_kwargs: dict[str, Any] = {"base_url": base_url}
        else:
            client_kwargs = {}
        return OpenAIChatModel(
            model_name=model_name, api_key=api_key, client_kwargs=client_kwargs
        )
    elif model_type == "dashscope":
        # qwen3.5-* series requires the MultiModalConversation endpoint even
        # for text-only usage.  The upstream agentscope SDK only auto-detects
        # "-vl" / "qvq" prefixes, so we explicitly opt-in here.
        multimodality: bool | None = None
        if model_name.startswith("qwen3.5"):
            multimodality = True
        stream = not model_name.startswith("qwen3.5")
        return DashScopeChatModel(
            model_name=model_name,
            api_key=api_key,
            multimodality=multimodality,
            stream=stream,
        )
    elif model_type == "anthropic":
        return AnthropicChatModel(model_name=model_name, api_key=api_key)
    elif model_type == "gemini":
        return GeminiChatModel(model_name=model_name, api_key=api_key)
    else:
        raise ValueError(f"Unknown model_type: {model_type}")


def create_formatter(model_type: str, model_name: str) -> FormatterBase:
    """Create formatter matching model type."""
    if model_type == "openai":
        if "grok" in model_name:
            return GrokChatFormatter()
        else:
            return OpenAIChatFormatter()
    elif model_type == "grok":
        return GrokChatFormatter()
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
