"""Agent implementations for NCAA betting.

This module provides the NCAA-specific BettingAgent with NCAA event formatting.
"""

import logging
from typing import Any

from dojozero.betting import (
    BettingAgent as BaseBettingAgent,
    BettingAgentConfig,
)
from dojozero.core import RuntimeContext

from dojozero.ncaa._formatters import format_event

logger = logging.getLogger(__name__)


class BettingAgent(BaseBettingAgent):
    """NCAA-specific BettingAgent with NCAA event formatting."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("event_formatter") is None:
            kwargs["event_formatter"] = format_event
        super().__init__(*args, **kwargs)

    @classmethod
    def from_dict(
        cls,
        config: BettingAgentConfig,
        context: RuntimeContext,
    ) -> "BettingAgent":
        """Create NCAA BettingAgent from config dict with NCAA formatter."""
        from dojozero.agents import (
            create_model,
            create_formatter,
        )

        actor_id = config["actor_id"]

        llm_config = config.get("llm", {})
        if not llm_config:
            raise ValueError(f"Missing 'llm' config for agent {actor_id}")
        model_type = llm_config.get("model_type", "openai")
        model_name = llm_config.get("model_name", "")
        return cls(
            actor_id=actor_id,
            trial_id=context.trial_id,
            name=config.get("name", actor_id),
            sys_prompt=config.get("sys_prompt", ""),
            model=create_model(llm_config),
            formatter=create_formatter(model_type, model_name),
            event_formatter=format_event,
        )


__all__ = ["BettingAgent", "BettingAgentConfig"]
