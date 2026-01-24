"""Agent implementations for NBA betting.

This module provides the NBA-specific BettingAgent with NBA event formatting.
"""

import logging
from typing import Any

from dojozero.betting import (
    BettingAgent as BaseBettingAgent,
    BettingAgentConfig,
)
from dojozero.core import RuntimeContext

from dojozero.nba._formatters import format_event

logger = logging.getLogger(__name__)


class BettingAgent(BaseBettingAgent):
    """NBA-specific BettingAgent with NBA event formatting.

    This class extends the shared BettingAgent to use NBA-specific
    event formatters by default.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # If no event_formatter provided, use NBA-specific formatter
        if kwargs.get("event_formatter") is None:
            kwargs["event_formatter"] = format_event
        super().__init__(*args, **kwargs)

    @classmethod
    def from_dict(
        cls,
        config: BettingAgentConfig,
        context: RuntimeContext,
    ) -> "BettingAgent":
        """Create NBA BettingAgent from config dict with NBA formatter.

        Note: agent_config_path is no longer supported here - the trial builder
        handles YAML loading and expansion. This method expects inline configs
        with a single LLMConfig.
        """
        from dojozero.agents import (
            create_model,
            create_formatter,
        )

        actor_id = config["actor_id"]

        # Inline config mode (already expanded by trial builder)
        llm_config = config.get("llm", {})
        model_type = llm_config.get("model_type", "openai")
        return cls(
            actor_id=actor_id,
            trial_id=context.trial_id,
            name=config.get("name", actor_id),
            sys_prompt=config.get("sys_prompt", ""),
            model=create_model(llm_config),
            formatter=create_formatter(model_type),
            event_formatter=format_event,
        )


__all__ = ["BettingAgent", "BettingAgentConfig"]
