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
        # Lookup maps populated from boxscore data for PBP enrichment
        self._team_tricode_lookup: dict[str, str] = {}  # team_id -> tricode
        self._team_name_lookup: dict[str, str] = {}  # team_id -> display name
        self._player_name_lookup: dict[int, str] = {}  # player_id -> name
        # Starters extracted from boxscore (starter=True) for GameStartEvent
        self._home_starters: dict[str, list[dict[str, Any]]] = {}  # game_id -> players
        self._away_starters: dict[str, list[dict[str, Any]]] = {}  # game_id -> players
        # Home/away team IDs per game for GameResultEvent team name lookup
        self._home_team_id: dict[str, str] = {}  # game_id -> team_id
        self._away_team_id: dict[str, str] = {}  # game_id -> team_id

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

    def update_team_lookup(self, team_id: str, tricode: str, name: str = "") -> None:
        """Register team_id -> tricode and name mappings from boxscore data."""
        if team_id and tricode:
            self._team_tricode_lookup[team_id] = tricode
        if team_id and name:
            self._team_name_lookup[team_id] = name

    def update_player_lookup(self, player_id: int, name: str) -> None:
        """Register a player_id -> name mapping from boxscore data."""
        if player_id and name:
            self._player_name_lookup[player_id] = name

    def get_team_tricode(self, team_id: str) -> str:
        """Look up team tricode by team_id."""
        return self._team_tricode_lookup.get(team_id, "")

    def get_team_name(self, team_id: str) -> str:
        """Look up team display name by team_id."""
        return self._team_name_lookup.get(team_id, "")

    def get_player_name(self, player_id: int) -> str:
        """Look up player name by player_id."""
        return self._player_name_lookup.get(player_id, "")

    def set_team_ids(self, game_id: str, home_team_id: str, away_team_id: str) -> None:
        """Store home/away team IDs for a game."""
        self._home_team_id[game_id] = home_team_id
        self._away_team_id[game_id] = away_team_id

    def get_home_team_id(self, game_id: str) -> str:
        """Get home team ID for a game."""
        return self._home_team_id.get(game_id, "")

    def get_away_team_id(self, game_id: str) -> str:
        """Get away team ID for a game."""
        return self._away_team_id.get(game_id, "")

    def set_starters(
        self,
        game_id: str,
        home_starters: list[dict[str, Any]],
        away_starters: list[dict[str, Any]],
    ) -> None:
        """Store starting lineups extracted from boxscore data."""
        self._home_starters[game_id] = home_starters
        self._away_starters[game_id] = away_starters

    def get_home_starters(self, game_id: str) -> list[dict[str, Any]]:
        """Get home team starters for a game."""
        return self._home_starters.get(game_id, [])

    def get_away_starters(self, game_id: str) -> list[dict[str, Any]]:
        """Get away team starters for a game."""
        return self._away_starters.get(game_id, [])

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
