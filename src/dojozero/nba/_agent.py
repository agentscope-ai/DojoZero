"""Agent implementations for NBA betting.

This module provides the NBA-specific BettingAgent with NBA event formatting.
"""

import logging
from typing import Any

from dojozero.betting import (
    BettingAgent as BaseBettingAgent,
    BettingAgentConfig,
)
from dojozero.core import RuntimeContext, StreamEvent
from dojozero.data._models import DataEvent

from dojozero.nba._formatters import format_event

logger = logging.getLogger(__name__)


class BettingAgent(BaseBettingAgent):
    """NBA-specific BettingAgent with NBA event formatting.

    This class extends the shared BettingAgent to use NBA-specific
    event formatters by default and provides NBA-specific context tracking.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # If no event_formatter provided, use NBA-specific formatter
        if kwargs.get("event_formatter") is None:
            kwargs["event_formatter"] = format_event
        super().__init__(*args, **kwargs)
        # Initialize NBA-specific game context
        self._game_context = {
            "home_team": "",
            "away_team": "",
            "period": 0,
            "game_clock": "",
            "home_score": 0,
            "away_score": 0,
            "game_status": "not_started",
            "latest_odds": {},
        }

    def _update_game_context(self, events: list[StreamEvent[Any]]) -> None:
        """Update NBA game context from events."""
        for event in events:
            payload = event.payload
            if not isinstance(payload, DataEvent):
                continue

            event_type = getattr(payload, "event_type", "")

            if event_type == "game_initialize":
                self._game_context["home_team"] = getattr(payload, "home_team", "")
                self._game_context["away_team"] = getattr(payload, "away_team", "")
                self._game_context["game_status"] = "initialized"

            elif event_type == "game_start":
                self._game_context["game_status"] = "in_progress"

            elif event_type == "game_update":
                self._game_context["period"] = getattr(payload, "period", 0)
                self._game_context["game_clock"] = getattr(payload, "game_clock", "")
                home_team = getattr(payload, "home_team", {})
                away_team = getattr(payload, "away_team", {})
                if isinstance(home_team, dict):
                    self._game_context["home_score"] = home_team.get("score", 0)
                if isinstance(away_team, dict):
                    self._game_context["away_score"] = away_team.get("score", 0)

            elif event_type == "play_by_play":
                self._game_context["home_score"] = getattr(payload, "home_score", 0)
                self._game_context["away_score"] = getattr(payload, "away_score", 0)
                self._game_context["period"] = getattr(payload, "period", 0)
                self._game_context["game_clock"] = getattr(payload, "clock", "")

            elif event_type == "odds_update":
                self._game_context["latest_odds"] = {
                    "home": getattr(payload, "home_odds", 0),
                    "away": getattr(payload, "away_odds", 0),
                }

            elif event_type == "game_result":
                self._game_context["game_status"] = "finished"
                final_score = getattr(payload, "final_score", {})
                self._game_context["home_score"] = final_score.get("home", 0)
                self._game_context["away_score"] = final_score.get("away", 0)

    @classmethod
    def from_dict(
        cls,
        config: BettingAgentConfig,
        context: RuntimeContext,
    ) -> "BettingAgent":
        """Create NBA BettingAgent from config dict with NBA formatter.

        Note: persona_config_path and llm_config_path are no longer supported here -
        the trial builder handles YAML loading and expansion. This method expects
        inline configs with a single LLMConfig.
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
