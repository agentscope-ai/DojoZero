"""Game state tracking for NFL data store."""

from typing import Any

from dojozero.data._models import PlayerIdentity
from dojozero.data.espn._state_tracker import BaseGameStateTracker


class NFLGameStateTracker(BaseGameStateTracker):
    """Manages game state variables for NFLStore.

    Inherits shared lifecycle/status/poll-profile logic from BaseGameStateTracker.

    NFL-specific state:
    - _seen_drive_ids: Deduplication for drive events
    - _last_odds: Cache last odds to detect changes
    - _current_drive: Track current drive ID per game
    - _starters: Game-day starters per team
    - _last_valid_clock: Last valid game clock per game (to handle post-game invalid data)
    """

    def __init__(self) -> None:
        """Initialize all state tracking variables."""
        super().__init__()
        self._seen_drive_ids: set[str] = set()
        self._last_odds: dict[str, dict[str, Any]] = {}
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

    # -- Odds tracking --------------------------------------------------------

    def get_last_odds(self, event_id: str) -> dict[str, Any] | None:
        """Get last odds snapshot for change detection."""
        return self._last_odds.get(event_id)

    def set_last_odds(self, event_id: str, odds: dict[str, Any]) -> None:
        """Set last odds snapshot."""
        self._last_odds[event_id] = odds

    def odds_changed(self, event_id: str, new_odds: dict[str, Any]) -> bool:
        """Check if odds have changed since last update."""
        last_odds = self.get_last_odds(event_id)
        if last_odds is None:
            return True
        for key in ["spread", "overUnder", "homeMoneyLine", "awayMoneyLine"]:
            if last_odds.get(key) != new_odds.get(key):
                return True
        return False

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
