"""Game state tracking for NFL data store."""

from typing import Any

from dojozero.data._models import PlayerIdentity
from dojozero.data.espn._state_tracker import BaseGameStateTracker


class NFLGameStateTracker(BaseGameStateTracker):
    """Manages game state variables for NFLStore.

    Inherits shared lifecycle/status/poll-profile logic from BaseGameStateTracker.

    NFL-specific state:
    - _seen_drive_ids: Deduplication for drive events
    - _current_drive: Track current drive ID per game
    - _starters: Game-day starters per team
    - _last_valid_clock: Last valid game clock per game (to handle post-game invalid data)
    """

    def __init__(self) -> None:
        """Initialize all state tracking variables."""
        super().__init__()
        self._seen_drive_ids: set[str] = set()
        self._current_drive: dict[str, str] = {}
        # key = "{event_id}_{team_id}" -> list of starters
        self._starters: dict[str, list[PlayerIdentity]] = {}
        # Track last valid game clock per game
        self._last_valid_clock: dict[str, str] = {}

    # -- Period/clock tracking ------------------------------------------------

    def update_game_clock(self, event_id: str, period: int, clock: str) -> None:
        """Update the latest period and clock from summary.

        Only updates when period > 0 to preserve last valid state.
        This prevents invalid period=0/clock="" data from overwriting
        valid game state after game conclusion.
        """
        if period > 0:
            self._current_period[event_id] = period
            self._last_valid_clock[event_id] = clock

    def get_last_valid_period(self, event_id: str) -> int:
        """Get last valid period (quarter) for game."""
        return self._current_period.get(event_id, 0)

    def get_last_valid_clock(self, event_id: str) -> str:
        """Get last valid game clock for game."""
        return self._last_valid_clock.get(event_id, "")

    # -- Drive deduplication --------------------------------------------------

    def has_seen_drive(self, drive_id: str) -> bool:
        """Check if drive has been processed (deduplication)."""
        return drive_id in self._seen_drive_ids

    def mark_drive_seen(self, drive_id: str) -> None:
        """Mark drive as processed."""
        self._seen_drive_ids.add(drive_id)

    # -- Drive tracking -------------------------------------------------------

    def get_current_drive(self, event_id: str) -> str | None:
        """Get current drive ID for game."""
        return self._current_drive.get(event_id)

    def set_current_drive(self, event_id: str, drive_id: str) -> None:
        """Set current drive ID for game."""
        self._current_drive[event_id] = drive_id

    # -- Starters tracking -----------------------------------------------------

    def set_starters(
        self, event_id: str, team_id: str, starters: list[PlayerIdentity]
    ) -> None:
        """Store game-day starters for a team."""
        self._starters[f"{event_id}_{team_id}"] = starters

    def get_starters(self, event_id: str, team_id: str) -> list[PlayerIdentity]:
        """Get game-day starters for a team (empty list if not fetched yet)."""
        return self._starters.get(f"{event_id}_{team_id}", [])

    # -- Filtering ------------------------------------------------------------

    def filter_new_drives(
        self, event_id: str, drives: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Filter drives to only completed new ones (deduplication).

        Only returns drives that have a result (are complete).
        """
        new_drives = []
        for drive in drives:
            if not isinstance(drive, dict):
                continue
            drive_id = str(drive.get("id", ""))
            if not drive_id:
                continue
            if not drive.get("result"):
                continue
            full_drive_id = f"{event_id}_drive_{drive_id}"
            if not self.has_seen_drive(full_drive_id):
                new_drives.append(drive)
                self.mark_drive_seen(full_drive_id)
        return new_drives

    # -- Serialization (for checkpoint/resume) --------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize state tracker to dictionary for checkpointing.

        Extends base class serialization with NFL-specific state.
        Deduplication sets (_seen_drive_ids) are NOT saved - rebuilt from JSONL.

        Returns:
            Dictionary containing serializable state.
        """
        base_state = super().to_dict()
        base_state.update(
            {
                # NFL-specific lifecycle state
                "current_drive": dict(self._current_drive),
                "last_valid_clock": dict(self._last_valid_clock),
                # Note: _seen_drive_ids is NOT saved - rebuilt from JSONL on resume
                # Note: _starters can be re-fetched from API
            }
        )
        return base_state

    def load_from_dict(self, data: dict[str, Any]) -> None:
        """Restore state tracker from dictionary.

        Args:
            data: Dictionary from to_dict()
        """
        super().load_from_dict(data)
        # NFL-specific state
        self._current_drive = dict(data.get("current_drive", {}))
        self._last_valid_clock = dict(data.get("last_valid_clock", {}))
        # _seen_drive_ids left empty - will be rebuilt from JSONL
        # _starters left empty - will be re-fetched from API

    def rebuild_dedup_from_drive_ids(self, drive_ids: set[str]) -> None:
        """Rebuild NFL-specific deduplication set from drive IDs.

        Called during resume to restore deduplication state from JSONL events.

        Args:
            drive_ids: Set of drive IDs (e.g., "{event_id}_drive_{drive_id}")
                      that have already been processed.
        """
        self._seen_drive_ids = drive_ids
