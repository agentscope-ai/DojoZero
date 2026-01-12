"""Game state tracking for NFL data store."""

from typing import Any


class NFLGameStateTracker:
    """Manages game state variables for NFLStore.

    Tracks:
    - _previous_game_status: Track game status transitions (pre-game, live, finished)
    - _seen_play_ids: Deduplication for play-by-play events
    - _seen_drive_ids: Deduplication for drive events
    - _game_started: Track when games have started (first play detected)
    - _initialized_games: Track which games have emitted NFLGameInitializeEvent
    - _last_odds: Cache last odds to detect changes
    - _current_drive: Track current drive ID per game

    This separation of concerns improves testability and makes state management explicit.
    """

    # Game status codes
    STATUS_SCHEDULED = 1
    STATUS_IN_PROGRESS = 2
    STATUS_FINAL = 3

    def __init__(self):
        """Initialize all state tracking variables."""
        self._previous_game_status: dict[str, int] = {}  # event_id -> status code
        self._seen_play_ids: set[str] = set()  # Set of processed play_ids
        self._seen_drive_ids: set[str] = set()  # Set of processed drive_ids
        self._game_started: set[str] = set()  # event_id -> True when first play seen
        self._initialized_games: set[str] = (
            set()
        )  # event_id -> True when NFLGameInitializeEvent emitted
        self._last_odds: dict[
            str, dict[str, Any]
        ] = {}  # event_id -> last odds snapshot
        self._current_drive: dict[str, str] = {}  # event_id -> current drive_id
        self._final_update_emitted: set[str] = (
            set()
        )  # event_id -> True when final NFLGameUpdateEvent emitted

    def get_previous_status(self, event_id: str) -> int | None:
        """Get previous game status for transition detection.

        Args:
            event_id: ESPN event ID

        Returns:
            Previous status code (1=scheduled, 2=in_progress, 3=final) or None
        """
        return self._previous_game_status.get(event_id)

    def set_previous_status(self, event_id: str, status: int) -> None:
        """Set previous game status.

        Args:
            event_id: ESPN event ID
            status: Game status code (1=scheduled, 2=in_progress, 3=final)
        """
        self._previous_game_status[event_id] = status

    def is_game_concluded(self, event_id: str) -> bool:
        """Check if game has concluded (status = FINAL).

        Args:
            event_id: ESPN event ID

        Returns:
            True if game status is FINAL
        """
        return self._previous_game_status.get(event_id) == self.STATUS_FINAL

    def has_final_update_emitted(self, event_id: str) -> bool:
        """Check if final game update has been emitted.

        Args:
            event_id: ESPN event ID

        Returns:
            True if final NFLGameUpdateEvent has been emitted
        """
        return event_id in self._final_update_emitted

    def mark_final_update_emitted(self, event_id: str) -> None:
        """Mark that final game update has been emitted.

        Args:
            event_id: ESPN event ID
        """
        self._final_update_emitted.add(event_id)

    def has_seen_play(self, play_id: str) -> bool:
        """Check if play has been processed (deduplication).

        Args:
            play_id: Play ID to check

        Returns:
            True if play has been seen before
        """
        return play_id in self._seen_play_ids

    def mark_play_seen(self, play_id: str) -> None:
        """Mark play as processed.

        Args:
            play_id: Play ID to mark as seen
        """
        self._seen_play_ids.add(play_id)

    def has_seen_drive(self, drive_id: str) -> bool:
        """Check if drive has been processed (deduplication).

        Args:
            drive_id: Drive ID to check

        Returns:
            True if drive has been seen before
        """
        return drive_id in self._seen_drive_ids

    def mark_drive_seen(self, drive_id: str) -> None:
        """Mark drive as processed.

        Args:
            drive_id: Drive ID to mark as seen
        """
        self._seen_drive_ids.add(drive_id)

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
        """Check if NFLGameInitializeEvent has been emitted.

        Args:
            event_id: ESPN event ID

        Returns:
            True if NFLGameInitializeEvent has been emitted
        """
        return event_id in self._initialized_games

    def mark_game_initialized(self, event_id: str) -> None:
        """Mark game as initialized (NFLGameInitializeEvent emitted).

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

    def get_current_drive(self, event_id: str) -> str | None:
        """Get current drive ID for game.

        Args:
            event_id: ESPN event ID

        Returns:
            Current drive ID or None
        """
        return self._current_drive.get(event_id)

    def set_current_drive(self, event_id: str, drive_id: str) -> None:
        """Set current drive ID for game.

        Args:
            event_id: ESPN event ID
            drive_id: Current drive ID
        """
        self._current_drive[event_id] = drive_id

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

    def filter_new_drives(
        self, event_id: str, drives: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Filter drives to only completed new ones (deduplication).

        Only returns drives that have a result (are complete).

        Args:
            event_id: ESPN event ID
            drives: List of drives from API

        Returns:
            List of new (unseen) completed drives
        """
        new_drives = []
        for drive in drives:
            if not isinstance(drive, dict):
                continue
            drive_id = str(drive.get("id", ""))
            if not drive_id:
                continue
            # Only emit drives that have a result (are complete)
            if not drive.get("result"):
                continue
            full_drive_id = f"{event_id}_drive_{drive_id}"
            if not self.has_seen_drive(full_drive_id):
                new_drives.append(drive)
                self.mark_drive_seen(full_drive_id)
        return new_drives
