"""ESPN game state tracking base class."""

from typing import Any


class ESPNStateTracker:
    """Base state tracker for ESPN data stores.

    Tracks:
    - Game status transitions (scheduled -> in_progress -> final)
    - Play deduplication
    - Game initialization status
    - Odds change detection

    Sport-specific trackers can extend this with additional state.
    """

    # Game status codes (common across ESPN sports)
    STATUS_SCHEDULED = 1
    STATUS_IN_PROGRESS = 2
    STATUS_FINAL = 3
    # Special status codes for abnormal game states
    STATUS_POSTPONED = 4
    STATUS_CANCELLED = 5

    # ESPN status name to code mapping
    # Note: Postponed/Cancelled are mapped to their specific codes (4, 5)
    # so the scheduler can detect and handle these abnormal states
    STATUS_NAME_MAP = {
        "STATUS_SCHEDULED": STATUS_SCHEDULED,
        "STATUS_IN_PROGRESS": STATUS_IN_PROGRESS,
        "STATUS_HALFTIME": STATUS_IN_PROGRESS,
        "STATUS_END_PERIOD": STATUS_IN_PROGRESS,
        "STATUS_DELAYED": STATUS_IN_PROGRESS,
        "STATUS_RAIN_DELAY": STATUS_IN_PROGRESS,
        "STATUS_SUSPENDED": STATUS_IN_PROGRESS,
        "STATUS_FINAL": STATUS_FINAL,
        "STATUS_FINAL_OVERTIME": STATUS_FINAL,
        "STATUS_POSTPONED": STATUS_POSTPONED,
        "STATUS_CANCELED": STATUS_CANCELLED,
    }

    def __init__(self):
        """Initialize all state tracking variables."""
        self._previous_game_status: dict[str, int] = {}  # event_id -> status code
        self._seen_play_ids: set[str] = set()  # Set of processed play IDs
        self._game_started: set[str] = set()  # event_ids where first play was seen
        self._initialized_games: set[str] = set()  # event_ids with init event emitted
        self._last_odds: dict[str, dict[str, Any]] = {}  # event_id -> last odds

    def get_previous_status(self, event_id: str) -> int | None:
        """Get previous game status for transition detection.

        Args:
            event_id: ESPN event ID

        Returns:
            Previous status code or None if not seen
        """
        return self._previous_game_status.get(event_id)

    def set_previous_status(self, event_id: str, status: int) -> None:
        """Set previous game status.

        Args:
            event_id: ESPN event ID
            status: Game status code
        """
        self._previous_game_status[event_id] = status

    def status_name_to_code(self, status_name: str) -> int:
        """Convert ESPN status name to status code.

        Args:
            status_name: ESPN status name (e.g., "STATUS_SCHEDULED")

        Returns:
            Status code (1=scheduled, 2=in_progress, 3=final)
        """
        return self.STATUS_NAME_MAP.get(status_name, self.STATUS_SCHEDULED)

    def has_seen_play(self, play_id: str) -> bool:
        """Check if play has been processed (deduplication).

        Args:
            play_id: Full play ID (should include event_id for uniqueness)

        Returns:
            True if play has been seen before
        """
        return play_id in self._seen_play_ids

    def mark_play_seen(self, play_id: str) -> None:
        """Mark play as processed.

        Args:
            play_id: Full play ID
        """
        self._seen_play_ids.add(play_id)

    def has_game_started(self, event_id: str) -> bool:
        """Check if game has started (first play detected).

        Args:
            event_id: ESPN event ID

        Returns:
            True if first play has been detected
        """
        return event_id in self._game_started

    def mark_game_started(self, event_id: str) -> None:
        """Mark game as started (first play detected).

        Args:
            event_id: ESPN event ID
        """
        self._game_started.add(event_id)

    def is_game_initialized(self, event_id: str) -> bool:
        """Check if game initialization event has been emitted.

        Args:
            event_id: ESPN event ID

        Returns:
            True if init event has been emitted
        """
        return event_id in self._initialized_games

    def mark_game_initialized(self, event_id: str) -> None:
        """Mark game as initialized.

        Args:
            event_id: ESPN event ID
        """
        self._initialized_games.add(event_id)

    def get_last_odds(self, event_id: str) -> dict[str, Any] | None:
        """Get last odds snapshot for change detection.

        Args:
            event_id: ESPN event ID

        Returns:
            Last odds dict or None
        """
        return self._last_odds.get(event_id)

    def set_last_odds(self, event_id: str, odds: dict[str, Any]) -> None:
        """Set last odds snapshot.

        Args:
            event_id: ESPN event ID
            odds: Odds data dict
        """
        self._last_odds[event_id] = odds

    def odds_changed(self, event_id: str, new_odds: dict[str, Any]) -> bool:
        """Check if odds have changed since last update.

        Args:
            event_id: ESPN event ID
            new_odds: New odds data

        Returns:
            True if odds have changed (or first time seeing odds)
        """
        last_odds = self.get_last_odds(event_id)
        if last_odds is None:
            return True

        # Compare key odds fields
        for key in ["spread", "overUnder", "homeMoneyLine", "awayMoneyLine"]:
            if last_odds.get(key) != new_odds.get(key):
                return True
        return False

    def filter_new_plays(
        self, event_id: str, plays: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Filter plays to only new ones (deduplication).

        Args:
            event_id: ESPN event ID
            plays: List of plays from API

        Returns:
            List of new (unseen) plays
        """
        new_plays = []
        for play in plays:
            if not isinstance(play, dict):
                continue
            play_id = str(play.get("id", ""))
            if not play_id:
                continue
            full_play_id = f"{event_id}_play_{play_id}"
            if not self.has_seen_play(full_play_id):
                new_plays.append(play)
                self.mark_play_seen(full_play_id)
        return new_plays

    def clear_game_state(self, event_id: str) -> None:
        """Clear all state for a game (useful for testing or reset).

        Args:
            event_id: ESPN event ID
        """
        self._previous_game_status.pop(event_id, None)
        self._game_started.discard(event_id)
        self._initialized_games.discard(event_id)
        self._last_odds.pop(event_id, None)
        # Note: We don't clear seen plays as they should remain deduplicated
