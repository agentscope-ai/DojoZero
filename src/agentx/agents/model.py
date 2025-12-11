import os

from agentscope.formatter import (
    FormatterBase,
    OpenAIChatFormatter,
    DashScopeChatFormatter,
)
from agentscope.model import ChatModelBase, OpenAIChatModel, DashScopeChatModel

from agentx.agents.config import (
    BettingAgentConfig,
    LLMConfig,
    get_api_key,
    get_base_url,
)


def _create_model_from_llm_config(llm_config: LLMConfig) -> ChatModelBase:
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


def _create_model(config: BettingAgentConfig) -> ChatModelBase:
    """Create model from config."""
    model_type = config.get("model_type", "openai")
    model_name = config.get("model_name", "qwen3-max")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", None)

    if model_type == "openai":
        client_args = {"base_url": base_url} if base_url else None
        return OpenAIChatModel(
            model_name=model_name, api_key=api_key, client_args=client_args
        )
    elif model_type == "dashscope":
        return DashScopeChatModel(model_name=model_name, api_key=api_key)
    else:
        raise ValueError(f"Unknown model_type: {model_type}")


def _create_formatter(model_type: str) -> FormatterBase:
    """Create formatter matching model type."""
    if model_type == "openai":
        return OpenAIChatFormatter()
    elif model_type == "dashscope":
        return DashScopeChatFormatter()
    else:
        raise ValueError(f"Unknown model_type: {model_type}")
