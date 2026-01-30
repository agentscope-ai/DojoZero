"""Game state tracking for NBA data store."""

from typing import Any

from dojozero.data._models import PollProfile


class GameStateTracker:
    """Manages game state variables for NBAStore.

    Extracts and encapsulates the 5 state management variables from NBAStore:
    - _previous_game_status: Track game status transitions (pre-game, live, finished)
    - _seen_event_ids: Deduplication for play-by-play events
    - _pbp_available: Detect when play-by-play becomes available (game start signal)
    - _boxscore_leaders_cache: Cache boxscore leaders to avoid redundant processing
    - _initialized_games: Track which games have emitted GameInitializeEvent

    This separation of concerns improves testability and makes state management explicit.
    """

    # Game status codes
    STATUS_SCHEDULED = 1
    STATUS_IN_PROGRESS = 2
    STATUS_FINAL = 3

    def __init__(self):
        """Initialize all state tracking variables."""
        self._previous_game_status: dict[str, int] = {}  # game_id -> gameStatus
        self._seen_event_ids: set[str] = set()  # Set of processed event_ids
        self._pbp_available: set[str] = (
            set()
        )  # game_id -> True when PBP first becomes available
        self._boxscore_leaders_cache: dict[
            str, dict[str, Any]
        ] = {}  # game_id -> leaders dict
        self._initialized_games: set[str] = (
            set()
        )  # game_id -> True when GameInitializeEvent emitted
        self._final_update_emitted: set[str] = (
            set()
        )  # game_id -> True when final NBAGameUpdateEvent emitted
        # Latest period/clock from play-by-play (used by boxscore updates)
        self._current_period: dict[str, int] = {}
        self._current_clock: dict[str, str] = {}
        # Latest scores (used for poll profile calculation)
        self._current_home_score: dict[str, int] = {}
        self._current_away_score: dict[str, int] = {}

    def get_previous_status(self, game_id: str) -> int | None:
        """Get previous game status for transition detection.

        Args:
            game_id: NBA game ID

        Returns:
            Previous status code (1=pre-game, 2=live, 3=finished) or None
        """
        return self._previous_game_status.get(game_id)

    def set_previous_status(self, game_id: str, status: int) -> None:
        """Set previous game status.

        Args:
            game_id: NBA game ID
            status: Game status code (1=pre-game, 2=live, 3=finished)
        """
        self._previous_game_status[game_id] = status

    def is_game_concluded(self, game_id: str) -> bool:
        """Check if game has concluded (status = FINAL).

        Args:
            game_id: NBA game ID

        Returns:
            True if game status is FINAL
        """
        return self._previous_game_status.get(game_id) == self.STATUS_FINAL

    def has_final_update_emitted(self, game_id: str) -> bool:
        """Check if final game update has been emitted.

        Args:
            game_id: NBA game ID

        Returns:
            True if final NBAGameUpdateEvent has been emitted
        """
        return game_id in self._final_update_emitted

    def mark_final_update_emitted(self, game_id: str) -> None:
        """Mark that final game update has been emitted.

        Args:
            game_id: NBA game ID
        """
        self._final_update_emitted.add(game_id)

    def has_seen_event(self, event_id: str) -> bool:
        """Check if event has been processed (deduplication).

        Args:
            event_id: Event ID to check

        Returns:
            True if event has been seen before
        """
        return event_id in self._seen_event_ids

    def mark_event_seen(self, event_id: str) -> None:
        """Mark event as processed.

        Args:
            event_id: Event ID to mark as seen
        """
        self._seen_event_ids.add(event_id)

    def is_pbp_available(self, game_id: str) -> bool:
        """Check if play-by-play is available for game.

        Args:
            game_id: NBA game ID

        Returns:
            True if play-by-play has become available
        """
        return game_id in self._pbp_available

    def mark_pbp_available(self, game_id: str) -> None:
        """Mark play-by-play as available (game start signal).

        Args:
            game_id: NBA game ID
        """
        self._pbp_available.add(game_id)

    def get_boxscore_cache(self, game_id: str) -> dict[str, Any] | None:
        """Get cached boxscore leaders.

        Args:
            game_id: NBA game ID

        Returns:
            Cached leaders dict or None
        """
        return self._boxscore_leaders_cache.get(game_id)

    def set_boxscore_cache(self, game_id: str, leaders: dict[str, Any]) -> None:
        """Cache boxscore leaders to avoid redundant processing.

        Args:
            game_id: NBA game ID
            leaders: Boxscore leaders dict
        """
        self._boxscore_leaders_cache[game_id] = leaders

    def is_game_initialized(self, game_id: str) -> bool:
        """Check if GameInitializeEvent has been emitted.

        Args:
            game_id: NBA game ID

        Returns:
            True if GameInitializeEvent has been emitted
        """
        return game_id in self._initialized_games

    def mark_game_initialized(self, game_id: str) -> None:
        """Mark game as initialized (GameInitializeEvent emitted).

        Args:
            game_id: NBA game ID
        """
        self._initialized_games.add(game_id)

    def update_game_clock(self, game_id: str, period: int, clock: str) -> None:
        """Update the latest period and clock from play-by-play.

        Args:
            game_id: NBA game ID
            period: Current period number (1-4, 5+ for OT)
            clock: Display clock string (e.g., "5:42")
        """
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

    # Close game threshold for LATE_GAME poll profile
    CLOSE_GAME_MARGIN = 10
    # Period threshold for late game (4Q and OT)
    LATE_GAME_PERIOD = 4

    def get_poll_profile(self, game_id: str) -> PollProfile:
        """Determine polling profile based on game state.

        Returns:
            PollProfile based on game status, period, and score margin.
        """
        status = self._previous_game_status.get(game_id)
        if status == self.STATUS_FINAL:
            return PollProfile.POST_GAME
        if status == self.STATUS_IN_PROGRESS:
            period = self._current_period.get(game_id, 0)
            if period >= self.LATE_GAME_PERIOD:
                home = self._current_home_score.get(game_id, 0)
                away = self._current_away_score.get(game_id, 0)
                if abs(home - away) <= self.CLOSE_GAME_MARGIN:
                    return PollProfile.LATE_GAME
            return PollProfile.IN_GAME
        return PollProfile.PRE_GAME

    def filter_new_actions(
        self, game_id: str, actions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Filter actions to only new ones (deduplication).

        Args:
            game_id: NBA game ID
            actions: List of play-by-play actions from API

        Returns:
            List of new (unseen) actions
        """
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
