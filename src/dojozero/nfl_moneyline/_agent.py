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
        if "event_formatter" not in kwargs:
            kwargs["event_formatter"] = format_event
        super().__init__(*args, **kwargs)

    @classmethod
    def from_dict(
        cls,
        config: BettingAgentConfig,
        context: RuntimeContext,
    ) -> "BettingAgent":
        """Create NFL BettingAgent from config dict with NFL formatter."""
        from dojozero.agents import (
            load_agent_config,
            create_model,
            create_formatter,
        )

        actor_id = config["actor_id"]
        agent_config_path = config.get("agent_config_path")

        if agent_config_path:
            # Load from YAML file
            yaml_config = load_agent_config(agent_config_path)
            llm_config = yaml_config["llm"]
            model_type = llm_config.get("model_type", "openai")
            return cls(
                actor_id=actor_id,
                trial_id=context.trial_id,
                name=yaml_config.get("name", actor_id),
                sys_prompt=yaml_config.get("sys_prompt", ""),
                model=create_model(llm_config),
                formatter=create_formatter(model_type),
                event_formatter=format_event,
            )

        # Inline config mode
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

    @classmethod
    def from_yaml(
        cls,
        config_path: str,
        actor_id: str,
        trial_id: str,
        toolkit: Any | None = None,
    ) -> "BettingAgent":
        """Create NFL BettingAgent from YAML config file with NFL formatter.

        Args:
            config_path: Path to YAML config file
            actor_id: The actor ID for this agent
            trial_id: The trial ID for this agent
            toolkit: Optional toolkit to use
        """
        from pathlib import Path

        from dojozero.agents import (
            load_agent_config,
            create_model,
            create_formatter,
        )

        path = Path(config_path) if isinstance(config_path, str) else config_path
        config = load_agent_config(path)
        llm_config = config["llm"]
        model_type = llm_config.get("model_type", "openai")

        return cls(
            actor_id=actor_id,
            trial_id=trial_id,
            name=config["name"],
            sys_prompt=config["sys_prompt"],
            model=create_model(llm_config),
            formatter=create_formatter(model_type),
            toolkit=toolkit,
            event_formatter=format_event,
        )


# Re-export BettingAgentConfig for convenience
__all__ = ["BettingAgent", "BettingAgentConfig"]
