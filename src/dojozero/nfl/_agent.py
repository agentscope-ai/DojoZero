"""Agent implementations for NFL betting.

This module provides NFL-specific BettingAgent with NFL event formatting.
"""

import logging
from typing import Any

from dojozero.betting import (
    BettingAgent as BaseBettingAgent,
    BettingAgentConfig,
)
from dojozero.core import RuntimeContext, StreamEvent
from dojozero.data._models import DataEvent

from dojozero.nfl._formatters import format_event

logger = logging.getLogger(__name__)


class BettingAgent(BaseBettingAgent):
    """NFL-specific BettingAgent with NFL event formatting.

    This class extends the shared BettingAgent to use NFL-specific
    event formatters by default and provides NFL-specific context tracking.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # If no event_formatter provided, use NFL-specific formatter
        if kwargs.get("event_formatter") is None:
            kwargs["event_formatter"] = format_event
        super().__init__(*args, **kwargs)
        # Initialize NFL-specific game context
        self._game_context = {
            "home_team": "",
            "away_team": "",
            "week": 0,
            "quarter": 0,
            "game_clock": "",
            "home_score": 0,
            "away_score": 0,
            "possession": "",
            "down": 0,
            "distance": 0,
            "yard_line": "",
            "game_status": "not_started",
            "latest_odds": {},
        }

    def _update_game_context(self, events: list[StreamEvent[Any]]) -> None:
        """Update NFL game context from events."""
        for event in events:
            payload = event.payload
            if not isinstance(payload, DataEvent):
                continue

            event_type = getattr(payload, "event_type", "")

            if event_type == "nfl_game_initialize":
                self._game_context["home_team"] = getattr(payload, "home_team", "")
                self._game_context["away_team"] = getattr(payload, "away_team", "")
                self._game_context["week"] = getattr(payload, "week", 0)
                self._game_context["game_status"] = "initialized"

            elif event_type == "nfl_game_start":
                self._game_context["game_status"] = "in_progress"

            elif event_type == "nfl_game_update":
                self._game_context["quarter"] = getattr(payload, "quarter", 0)
                self._game_context["game_clock"] = getattr(payload, "game_clock", "")
                self._game_context["possession"] = getattr(payload, "possession", "")
                self._game_context["down"] = getattr(payload, "down", 0)
                self._game_context["distance"] = getattr(payload, "distance", 0)
                self._game_context["yard_line"] = getattr(payload, "yard_line", "")
                home_team = getattr(payload, "home_team", {})
                away_team = getattr(payload, "away_team", {})
                if isinstance(home_team, dict):
                    self._game_context["home_score"] = home_team.get("score", 0)
                if isinstance(away_team, dict):
                    self._game_context["away_score"] = away_team.get("score", 0)

            elif event_type == "nfl_play":
                self._game_context["home_score"] = getattr(payload, "home_score", 0)
                self._game_context["away_score"] = getattr(payload, "away_score", 0)
                self._game_context["quarter"] = getattr(payload, "quarter", 0)
                self._game_context["game_clock"] = getattr(payload, "game_clock", "")

            elif event_type == "nfl_odds_update":
                self._game_context["latest_odds"] = {
                    "home": getattr(payload, "home_odds", 0),
                    "away": getattr(payload, "away_odds", 0),
                }

            elif event_type == "nfl_game_result":
                self._game_context["game_status"] = "finished"
                self._game_context["home_score"] = getattr(payload, "home_score", 0)
                self._game_context["away_score"] = getattr(payload, "away_score", 0)

    def _get_context_summary(self) -> str:
        """Get NFL-specific game context summary."""
        ctx = self._game_context
        home = ctx.get("home_team", "?")
        away = ctx.get("away_team", "?")
        week = ctx.get("week", 0)
        status = ctx.get("game_status", "unknown")

        week_str = f" (Week {week})" if week else ""

        if status == "not_started":
            return f"Game: {away} @ {home}{week_str} - Not started yet. Events: {self._event_count}"

        if status == "initialized":
            return f"Game: {away} @ {home}{week_str} - Initialized. Events: {self._event_count}"

        quarter = ctx.get("quarter", 0)
        clock = ctx.get("game_clock", "")
        home_score = ctx.get("home_score", 0)
        away_score = ctx.get("away_score", 0)

        quarter_str = f"Q{quarter}" if quarter <= 4 else f"OT{quarter - 4}"

        if status == "finished":
            return f"Game: {away} @ {home}{week_str} - FINAL: {away_score}-{home_score}. Events: {self._event_count}"

        # Game situation
        down = ctx.get("down", 0)
        distance = ctx.get("distance", 0)
        yard_line = ctx.get("yard_line", "")
        possession = ctx.get("possession", "")

        situation = ""
        if down > 0:
            situation = f" | {down}&{distance} at {yard_line}"
            if possession:
                situation += f" ({possession} ball)"

        odds = ctx.get("latest_odds", {})
        odds_str = ""
        if odds:
            odds_str = (
                f" | Odds: Home {odds.get('home', '?')}, Away {odds.get('away', '?')}"
            )

        return f"Game: {away} @ {home}{week_str} | {quarter_str} {clock} | Score: {away_score}-{home_score}{situation}{odds_str}. Events: {self._event_count}"

    @classmethod
    def from_dict(
        cls,
        config: BettingAgentConfig,
        context: RuntimeContext,
    ) -> "BettingAgent":
        """Create NFL BettingAgent from config dict with NFL formatter.

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


# Re-export BettingAgentConfig for convenience
__all__ = ["BettingAgent", "BettingAgentConfig"]
