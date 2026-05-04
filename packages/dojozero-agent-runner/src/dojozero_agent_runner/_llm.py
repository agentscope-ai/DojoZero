"""Factories for the agentscope ``ChatModelBase`` and ``FormatterBase`` pair.

Mirrors the model selection logic in ``dojozero.agents._config`` so the
runner doesn't have to depend on the full ``dojozero`` package — its only
heavy dep is ``agentscope`` itself.
"""

from __future__ import annotations

import os
from typing import Any

from agentscope.formatter import (
    AnthropicChatFormatter,
    DashScopeChatFormatter,
    FormatterBase,
    GeminiChatFormatter,
    OpenAIChatFormatter,
)
from agentscope.model import (
    AnthropicChatModel,
    ChatModelBase,
    DashScopeChatModel,
    GeminiChatModel,
    OpenAIChatModel,
)


def create_model(llm_config: dict[str, Any]) -> ChatModelBase:
    """Build a ``ChatModelBase`` from a parsed LLM YAML entry.

    The LLM YAML entry must include ``model_type``, ``model_name``, and
    ``api_key_env`` (the env var name holding the provider's API key).
    """
    model_type = llm_config.get("model_type", "openai")
    model_name = llm_config.get("model_name", "")
    api_key_env = llm_config.get("api_key_env", "")
    if not api_key_env:
        raise ValueError("Missing 'api_key_env' in LLM config")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(f"API key environment variable '{api_key_env}' is not set.")

    base_url_env = llm_config.get("base_url_env")
    base_url = os.environ.get(base_url_env) if base_url_env else None
    if base_url_env and not base_url:
        raise ValueError(f"Base URL environment variable '{base_url_env}' is not set.")

    if model_type == "grok":
        return OpenAIChatModel(
            model_name=model_name,
            api_key=api_key,
            stream=False,
            client_kwargs={"base_url": "https://api.x.ai/v1"},
        )
    if model_type == "openai":
        client_kwargs: dict[str, Any] = {"base_url": base_url} if base_url else {}
        return OpenAIChatModel(
            model_name=model_name,
            api_key=api_key,
            stream=False,
            client_kwargs=client_kwargs,
        )
    if model_type == "dashscope":
        # qwen3.5-* requires the MultiModalConversation endpoint even for
        # text-only usage — match the same opt-in the in-process BettingAgent
        # does.
        multimodality: bool | None = True if model_name.startswith("qwen3.5") else None
        return DashScopeChatModel(
            model_name=model_name,
            api_key=api_key,
            multimodality=multimodality,
            stream=False,
        )
    if model_type == "anthropic":
        return AnthropicChatModel(
            model_name=model_name,
            api_key=api_key,
            stream=False,
        )
    if model_type == "gemini":
        return GeminiChatModel(
            model_name=model_name,
            api_key=api_key,
            stream=False,
        )
    raise ValueError(f"Unknown model_type: {model_type}")


def create_formatter(model_type: str) -> FormatterBase:
    """Build a ``FormatterBase`` matching the model's provider."""
    if model_type in ("openai", "grok"):
        return OpenAIChatFormatter()
    if model_type == "dashscope":
        return DashScopeChatFormatter()
    if model_type == "anthropic":
        return AnthropicChatFormatter()
    if model_type == "gemini":
        return GeminiChatFormatter()
    raise ValueError(f"Unknown model_type: {model_type}")


__all__ = ["create_formatter", "create_model"]
