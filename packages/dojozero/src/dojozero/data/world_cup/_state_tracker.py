"""Game state tracking for the World Cup data store."""

from typing import Any

from dojozero.data.espn._state_tracker import BaseGameStateTracker
from dojozero.data.world_cup._constants import SOCCER_STATUS_NAME_MAP


class GameStateTracker(BaseGameStateTracker):
    """Manages match state for ``WorldCupStore``.

    Soccer-specific state on top of the shared base tracker:
    - ``_team_tricode_lookup``: team_id → 3-letter code (e.g. "ARG")
    - ``_team_name_lookup``: team_id → display name
    - ``_player_name_lookup``: player_id → display name
    - ``_home_team_id`` / ``_away_team_id``: per-game team IDs for result events
    - ``_home_starters`` / ``_away_starters``: starting XI from rosters
    - ``_pbp_available``: per-game flag set on first play (kickoff signal)
    - ``_current_clock``: latest clock displayValue from PBP

    Soccer uses periods 1–2 (halves), 3–4 (extra time), 5 (penalty shootout).
    Late-game polling kicks in from period 2 onward.
    """

    # Late game starts in 2nd half; a 2-goal margin is the close-game threshold.
    LATE_GAME_PERIOD = 2
    CLOSE_GAME_MARGIN = 2

    def __init__(self) -> None:
        super().__init__()
        self._team_tricode_lookup: dict[str, str] = {}
        self._team_name_lookup: dict[str, str] = {}
        self._player_name_lookup: dict[str, str] = {}
        self._home_team_id: dict[str, str] = {}
        self._away_team_id: dict[str, str] = {}
        self._winner_side: dict[str, str] = {}
        self._final_summary_seen: set[str] = set()
        self._home_starters: dict[str, list[dict[str, Any]]] = {}
        self._away_starters: dict[str, list[dict[str, Any]]] = {}
        self._pbp_available: set[str] = set()
        self._current_clock: dict[str, str] = {}

    def status_name_to_code(self, status_name: str) -> int:
        """Map ESPN soccer status names to status codes."""
        return SOCCER_STATUS_NAME_MAP.get(status_name, self.STATUS_SCHEDULED)

    def is_pbp_available(self, game_id: str) -> bool:
        return game_id in self._pbp_available

    def mark_pbp_available(self, game_id: str) -> None:
        self._pbp_available.add(game_id)

    def update_match_clock(self, game_id: str, period: int, clock: str) -> None:
        """Update latest period/clock from PBP; only stores valid periods."""
        if period > 0:
            self._current_period[game_id] = period
            self._current_clock[game_id] = clock

    def get_current_period(self, game_id: str) -> int:
        return self._current_period.get(game_id, 0)

    def get_current_clock(self, game_id: str) -> str:
        return self._current_clock.get(game_id, "")

    def update_scores(self, game_id: str, home_score: int, away_score: int) -> None:
        self._current_home_score[game_id] = home_score
        self._current_away_score[game_id] = away_score

    def get_current_scores(self, game_id: str) -> tuple[int, int]:
        return (
            self._current_home_score.get(game_id, 0),
            self._current_away_score.get(game_id, 0),
        )

    # -- Team / player lookup -------------------------------------------------

    def update_team_lookup(self, team_id: str, tricode: str, name: str = "") -> None:
        if team_id and tricode:
            self._team_tricode_lookup[team_id] = tricode
        if team_id and name:
            self._team_name_lookup[team_id] = name

    def update_player_lookup(self, player_id: str, name: str) -> None:
        if player_id and name:
            self._player_name_lookup[player_id] = name

    def get_team_tricode(self, team_id: str) -> str:
        return self._team_tricode_lookup.get(team_id, "")

    def get_team_name(self, team_id: str) -> str:
        return self._team_name_lookup.get(team_id, "")

    def get_player_name(self, player_id: str) -> str:
        return self._player_name_lookup.get(player_id, "")

    def set_team_ids(self, game_id: str, home_team_id: str, away_team_id: str) -> None:
        self._home_team_id[game_id] = home_team_id
        self._away_team_id[game_id] = away_team_id

    def get_home_team_id(self, game_id: str) -> str:
        return self._home_team_id.get(game_id, "")

    def get_away_team_id(self, game_id: str) -> str:
        return self._away_team_id.get(game_id, "")

    def set_winner_side(self, game_id: str, winner_side: str) -> None:
        if winner_side in {"home", "away"}:
            self._winner_side[game_id] = winner_side

    def get_winner_side(self, game_id: str) -> str:
        return self._winner_side.get(game_id, "")

    def has_final_summary_seen(self, game_id: str) -> bool:
        return game_id in self._final_summary_seen

    def mark_final_summary_seen(self, game_id: str) -> None:
        self._final_summary_seen.add(game_id)

    def set_starters(
        self,
        game_id: str,
        home_starters: list[dict[str, Any]],
        away_starters: list[dict[str, Any]],
    ) -> None:
        self._home_starters[game_id] = home_starters
        self._away_starters[game_id] = away_starters

    def get_home_starters(self, game_id: str) -> list[dict[str, Any]]:
        return self._home_starters.get(game_id, [])

    def get_away_starters(self, game_id: str) -> list[dict[str, Any]]:
        return self._away_starters.get(game_id, [])

    # -- Serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        base_state = super().to_dict()
        base_state.update(
            {
                "pbp_available": list(self._pbp_available),
                "current_clock": dict(self._current_clock),
                "home_team_id": dict(self._home_team_id),
                "away_team_id": dict(self._away_team_id),
                "winner_side": dict(self._winner_side),
                "final_summary_seen": list(self._final_summary_seen),
                # Lookup tables and starters are NOT saved — re-fetched on resume.
                # _seen_play_ids is NOT saved — rebuilt from JSONL on resume.
            }
        )
        return base_state

    def load_from_dict(self, data: dict[str, Any]) -> None:
        super().load_from_dict(data)
        self._pbp_available = set(data.get("pbp_available", []))
        self._current_clock = dict(data.get("current_clock", {}))
        self._home_team_id = dict(data.get("home_team_id", {}))
        self._away_team_id = dict(data.get("away_team_id", {}))
        self._winner_side = dict(data.get("winner_side", {}))
        self._final_summary_seen = set(data.get("final_summary_seen", []))


__all__ = ["GameStateTracker"]
