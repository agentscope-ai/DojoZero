"""Base game state tracking for ESPN-based data stores.

Provides shared state management logic used by both NBA and NFL state trackers:
game status transitions, initialization tracking, final-update dedup, period/score
tracking, adaptive poll profile calculation, and play deduplication.
"""

from typing import Any

from dojozero.data._models import PollProfile

# ESPN status name → code mapping (shared across all sports)
_STATUS_NAME_MAP: dict[str, int] = {
    "STATUS_SCHEDULED": 1,
    "STATUS_IN_PROGRESS": 2,
    "STATUS_HALFTIME": 2,
    "STATUS_END_PERIOD": 2,
    "STATUS_FINAL": 3,
    "STATUS_FINAL_OVERTIME": 3,
}


class BaseGameStateTracker:
    """Base state tracker for ESPN game data stores.

    Tracks shared game lifecycle state that is common across all ESPN sports:
    - Game status transitions (scheduled → in-progress → final)
    - Game initialization event emission
    - Final update event emission
    - Period and score tracking for adaptive poll profiles
    - Play deduplication and game-started detection

    Sport-specific subclasses add odds tracking, drive tracking, etc.
    """

    # Game status codes
    STATUS_SCHEDULED = 1
    STATUS_IN_PROGRESS = 2
    STATUS_FINAL = 3

    # Close game threshold for LATE_GAME poll profile
    CLOSE_GAME_MARGIN = 10
    # Period threshold for late game (4Q and OT)
    LATE_GAME_PERIOD = 4

    def __init__(self) -> None:
        """Initialize shared state tracking variables."""
        self._previous_game_status: dict[str, int] = {}
        self._initialized_games: set[str] = set()
        self._final_update_emitted: set[str] = set()
        self._current_period: dict[str, int] = {}
        self._current_home_score: dict[str, int] = {}
        self._current_away_score: dict[str, int] = {}
        self._seen_play_ids: set[str] = set()
        self._game_started: set[str] = set()
        # Track last emitted scores for score-change detection
        self._last_emitted_home_score: dict[str, int] = {}
        self._last_emitted_away_score: dict[str, int] = {}

    # -- Status tracking -----------------------------------------------------

    def status_name_to_code(self, status_name: str) -> int:
        """Convert ESPN status name to status code.

        Returns:
            Status code (1=scheduled, 2=in_progress, 3=final).
        """
        return _STATUS_NAME_MAP.get(status_name, self.STATUS_SCHEDULED)

    def get_previous_status(self, event_id: str) -> int | None:
        """Get previous game status for transition detection.

        Returns:
            Previous status code (1=scheduled, 2=in_progress, 3=final) or None.
        """
        return self._previous_game_status.get(event_id)

    def set_previous_status(self, event_id: str, status: int) -> None:
        """Set previous game status."""
        self._previous_game_status[event_id] = status

    def is_game_concluded(self, event_id: str) -> bool:
        """Check if game has concluded (status = FINAL)."""
        return self._previous_game_status.get(event_id) == self.STATUS_FINAL

    # -- Final update tracking ------------------------------------------------

    def has_final_update_emitted(self, event_id: str) -> bool:
        """Check if final game update event has been emitted."""
        return event_id in self._final_update_emitted

    def mark_final_update_emitted(self, event_id: str) -> None:
        """Mark that final game update event has been emitted."""
        self._final_update_emitted.add(event_id)

    # -- Initialization tracking ----------------------------------------------

    def is_game_initialized(self, event_id: str) -> bool:
        """Check if GameInitializeEvent has been emitted."""
        return event_id in self._initialized_games

    def mark_game_initialized(self, event_id: str) -> None:
        """Mark game as initialized (GameInitializeEvent emitted)."""
        self._initialized_games.add(event_id)

    # -- Period and score tracking --------------------------------------------

    def update_game_state(
        self, event_id: str, period: int, home_score: int, away_score: int
    ) -> None:
        """Update period and scores for poll profile calculation."""
        self._current_period[event_id] = period
        self._current_home_score[event_id] = home_score
        self._current_away_score[event_id] = away_score

    # -- Score change detection -----------------------------------------------

    def score_changed(self, event_id: str, home_score: int, away_score: int) -> bool:
        """Check if score has changed since last emission.

        Args:
            event_id: Game/event ID
            home_score: Current home score
            away_score: Current away score

        Returns:
            True if either score has changed since last emission.
        """
        last_home = self._last_emitted_home_score.get(event_id)
        last_away = self._last_emitted_away_score.get(event_id)

        # First time seeing this game - not a change, just initialization
        if last_home is None or last_away is None:
            return False

        return home_score != last_home or away_score != last_away

    def mark_scores_emitted(
        self, event_id: str, home_score: int, away_score: int
    ) -> None:
        """Mark that a game update was emitted with these scores.

        Call this after emitting a game update event so subsequent
        score_changed() calls can detect new scoring plays.
        """
        self._last_emitted_home_score[event_id] = home_score
        self._last_emitted_away_score[event_id] = away_score

    # -- Poll profile ---------------------------------------------------------

    def get_poll_profile(self, event_id: str) -> PollProfile:
        """Determine polling profile based on game state.

        Returns:
            PollProfile based on game status, period, and score margin.
        """
        status = self._previous_game_status.get(event_id)
        if status == self.STATUS_FINAL:
            return PollProfile.POST_GAME
        if status == self.STATUS_IN_PROGRESS:
            period = self._current_period.get(event_id, 0)
            if period >= self.LATE_GAME_PERIOD:
                home = self._current_home_score.get(event_id, 0)
                away = self._current_away_score.get(event_id, 0)
                if abs(home - away) <= self.CLOSE_GAME_MARGIN:
                    return PollProfile.LATE_GAME
            return PollProfile.IN_GAME
        return PollProfile.PRE_GAME

    # -- Play deduplication ---------------------------------------------------

    def has_game_started(self, event_id: str) -> bool:
        """Check if game has started (first play detected)."""
        return event_id in self._game_started

    def mark_game_started(self, event_id: str) -> None:
        """Mark game as started (first play detected)."""
        self._game_started.add(event_id)

    def has_seen_play(self, play_id: str) -> bool:
        """Check if play has been processed (deduplication)."""
        return play_id in self._seen_play_ids

    def mark_play_seen(self, play_id: str) -> None:
        """Mark play as processed."""
        self._seen_play_ids.add(play_id)

    def filter_new_plays(
        self, event_id: str, plays: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Filter plays to only new ones (deduplication).

        Returns:
            List of new (unseen) plays.
        """
        new_plays = []
        for play in plays:
            if not isinstance(play, dict):
                continue
            play_id = str(play.get("id", ""))
            if not play_id:
                continue
            full_play_id = f"{event_id}_play_{play_id}"
            if full_play_id not in self._seen_play_ids:
                new_plays.append(play)
                self._seen_play_ids.add(full_play_id)
        return new_plays

    # -- Serialization (for checkpoint/resume) --------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize state tracker to dictionary for checkpointing.

        Only saves critical lifecycle state. Deduplication sets can be
        rebuilt from JSONL on resume.

        Returns:
            Dictionary containing serializable state.
        """
        return {
            "previous_game_status": dict(self._previous_game_status),
            "initialized_games": list(self._initialized_games),
            "final_update_emitted": list(self._final_update_emitted),
            "game_started": list(self._game_started),
            "current_period": dict(self._current_period),
            "current_home_score": dict(self._current_home_score),
            "current_away_score": dict(self._current_away_score),
            "last_emitted_home_score": dict(self._last_emitted_home_score),
            "last_emitted_away_score": dict(self._last_emitted_away_score),
            # Note: _seen_play_ids is NOT saved - rebuilt from JSONL on resume
        }

    def load_from_dict(self, data: dict[str, Any]) -> None:
        """Restore state tracker from dictionary.

        Args:
            data: Dictionary from to_dict()
        """
        self._previous_game_status = dict(data.get("previous_game_status", {}))
        self._initialized_games = set(data.get("initialized_games", []))
        self._final_update_emitted = set(data.get("final_update_emitted", []))
        self._game_started = set(data.get("game_started", []))
        self._current_period = dict(data.get("current_period", {}))
        self._current_home_score = dict(data.get("current_home_score", {}))
        self._current_away_score = dict(data.get("current_away_score", {}))
        self._last_emitted_home_score = dict(data.get("last_emitted_home_score", {}))
        self._last_emitted_away_score = dict(data.get("last_emitted_away_score", {}))
        # _seen_play_ids left empty - will be rebuilt from JSONL

    def rebuild_dedup_from_play_ids(self, play_ids: set[str]) -> None:
        """Rebuild deduplication set from a collection of play IDs.

        Called during resume to restore deduplication state from JSONL events.

        Args:
            play_ids: Set of play IDs that have already been processed.
        """
        self._seen_play_ids = play_ids


__all__: list[str] = ["BaseGameStateTracker"]
