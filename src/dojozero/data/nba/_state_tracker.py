"""Game state tracking for NBA data store."""

from typing import Any

from dojozero.data.espn._state_tracker import BaseGameStateTracker


class GameStateTracker(BaseGameStateTracker):
    """Manages game state variables for NBAStore.

    Inherits shared lifecycle/status/poll-profile logic from BaseGameStateTracker.

    NBA-specific state:
    - _seen_event_ids: Deduplication for play-by-play events
    - _pbp_available: Detect when play-by-play becomes available (game start signal)
    - _boxscore_leaders_cache: Cache boxscore leaders to avoid redundant processing
    - _current_clock: Latest game clock from play-by-play
    """

    def __init__(self) -> None:
        """Initialize all state tracking variables."""
        super().__init__()
        self._seen_event_ids: set[str] = set()
        self._pbp_available: set[str] = set()
        self._boxscore_leaders_cache: dict[str, dict[str, Any]] = {}
        self._current_clock: dict[str, str] = {}

    def has_seen_event(self, event_id: str) -> bool:
        """Check if event has been processed (deduplication)."""
        return event_id in self._seen_event_ids

    def mark_event_seen(self, event_id: str) -> None:
        """Mark event as processed."""
        self._seen_event_ids.add(event_id)

    def is_pbp_available(self, game_id: str) -> bool:
        """Check if play-by-play is available for game."""
        return game_id in self._pbp_available

    def mark_pbp_available(self, game_id: str) -> None:
        """Mark play-by-play as available (game start signal)."""
        self._pbp_available.add(game_id)

    def get_boxscore_cache(self, game_id: str) -> dict[str, Any] | None:
        """Get cached boxscore leaders."""
        return self._boxscore_leaders_cache.get(game_id)

    def set_boxscore_cache(self, game_id: str, leaders: dict[str, Any]) -> None:
        """Cache boxscore leaders to avoid redundant processing."""
        self._boxscore_leaders_cache[game_id] = leaders

    def update_game_clock(self, game_id: str, period: int, clock: str) -> None:
        """Update the latest period and clock from play-by-play."""
        self._current_period[game_id] = period
        self._current_clock[game_id] = clock

    def get_current_period(self, game_id: str) -> int:
        """Get latest period from play-by-play."""
        return self._current_period.get(game_id, 0)

    def get_current_clock(self, game_id: str) -> str:
        """Get latest game clock from play-by-play."""
        return self._current_clock.get(game_id, "")

    def update_scores(self, game_id: str, home_score: int, away_score: int) -> None:
        """Update latest scores for poll profile calculation."""
        self._current_home_score[game_id] = home_score
        self._current_away_score[game_id] = away_score

    def filter_new_actions(
        self, game_id: str, actions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Filter actions to only new ones (deduplication)."""
        new_actions = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_number = action.get("actionNumber", 0)
            pbp_event_id = f"{game_id}_pbp_{action_number}"
            if not self.has_seen_event(pbp_event_id):
                new_actions.append(action)
                self.mark_event_seen(pbp_event_id)
        return new_actions
