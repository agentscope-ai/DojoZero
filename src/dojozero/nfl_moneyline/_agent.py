"""Agent implementations for NFL moneyline betting.

This module provides NFL-specific BettingAgent with NFL event formatting.
"""

import logging
from typing import Any

from dojozero.betting import (
    BettingAgent as BaseBettingAgent,
    BettingAgentConfig,
)
from dojozero.core import RuntimeContext

from dojozero.nfl_moneyline._formatters import format_event

logger = logging.getLogger(__name__)


class BettingAgent(BaseBettingAgent):
    """NFL-specific BettingAgent with NFL event formatting.

    This class extends the shared BettingAgent to use NFL-specific
    event formatters by default.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # If no event_formatter provided, use NFL-specific formatter
        if kwargs.get("event_formatter") is None:
            kwargs["event_formatter"] = format_event
        super().__init__(*args, **kwargs)

    @classmethod
    def from_dict(
        cls,
        config: BettingAgentConfig,
        context: RuntimeContext,
    ) -> "BettingAgent":
        """Create NFL BettingAgent from config dict with NFL formatter.

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


# Re-export BettingAgentConfig for convenience
__all__ = ["BettingAgent", "BettingAgentConfig"]
