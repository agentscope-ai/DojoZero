"""Model adapter for AgentJet's OpenAI-compatible API.

This module provides a wrapper to use AgentJet's API as an agentscope model,
enabling seamless integration with BettingAgent's ReActAgent.
"""

from typing import Any

from agentscope.formatter import OpenAIChatFormatter, FormatterBase
from agentscope.model import OpenAIChatModel, ChatModelBase


def create_agentjet_model(
    base_url: str,
    api_key: str,
    model_name: str = "agentjet-model",
) -> ChatModelBase:
    """Create an agentscope model that uses AgentJet's OpenAI-compatible API.

    AgentJet provides an OpenAI-compatible API endpoint during training,
    which we can use directly with agentscope's OpenAIChatModel.

    Args:
        base_url: AgentJet API base URL (e.g., "http://localhost:8000/v1")
        api_key: AgentJet API key
        model_name: Model name to use in API calls (default: "agentjet-model")

    Returns:
        ChatModelBase instance configured for AgentJet
    """
    client_kwargs: dict[str, Any] = {
        "base_url": base_url,
        "timeout": 120.0,
    }
    return OpenAIChatModel(
        model_name=model_name,
        api_key=api_key,
        client_kwargs=client_kwargs,
    )


def create_agentjet_formatter() -> FormatterBase:
    """Create a formatter for AgentJet's OpenAI-compatible API.

    Returns:
        FormatterBase instance for OpenAI format
    """
    return OpenAIChatFormatter()


class AgentJetModelWrapper:
    """Convenience wrapper holding both model and formatter for AgentJet.

    This class bundles the model and formatter together for easier passing
    to BettingAgent creation.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str = "agentjet-model",
    ):
        """Initialize the AgentJet model wrapper.

        Args:
            base_url: AgentJet API base URL
            api_key: AgentJet API key
            model_name: Model name for API calls
        """
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name
        self._model: ChatModelBase | None = None
        self._formatter: FormatterBase | None = None

    @property
    def model(self) -> ChatModelBase:
        """Lazily create and return the model."""
        if self._model is None:
            self._model = create_agentjet_model(
                base_url=self.base_url,
                api_key=self.api_key,
                model_name=self.model_name,
            )
        return self._model

    @property
    def formatter(self) -> FormatterBase:
        """Lazily create and return the formatter."""
        if self._formatter is None:
            self._formatter = create_agentjet_formatter()
        return self._formatter
