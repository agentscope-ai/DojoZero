"""NBA data store implementation."""

from typing import Any, Sequence

from dojozero.data._models import DataEvent
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data.nba._api import NBAExternalAPI
from dojozero.data.nba._events import (
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    GameUpdateEvent,
    PlayByPlayEvent,
)
from dojozero.data.nba._state_tracker import GameStateTracker


class NBAStore(DataStore):
    """NBA data store for polling NBA API and emitting events."""

    def __init__(
        self,
        store_id: str = "nba_store",
        api: ExternalAPI | None = None,
        poll_intervals: dict[str, float] | None = None,
        event_emitter=None,
    ):
        """Initialize NBA store.

        Default polling intervals:
        - boxscore: 60.0 seconds (for complete game updates with all leaders)
        - play_by_play: 20.0 seconds (for all play-by-play events and game status)
        """
        # Set default poll_intervals if not provided
        if poll_intervals is None:
            poll_intervals = {
                "boxscore": 60.0,  # Complete game updates with all leaders
                "play_by_play": 20.0,  # All play-by-play events and game status detection
            }

        super().__init__(
            store_id,
            api or NBAExternalAPI(),
            poll_intervals,
            event_emitter,
        )
        # Game state tracker manages all state variables in one place
        self._state = GameStateTracker()

    def _extract_player_stats_from_boxscore(
        self, boxscore_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract all player stats from BoxScore data.

        Args:
            boxscore_data: Full BoxScoreTraditionalV3 data from API

        Returns:
            Dictionary with structure:
            {
                "home": [list of player dicts with stats],
                "away": [list of player dicts with stats]
            }
        """
        home_team_data = boxscore_data.get("homeTeam", {})
        away_team_data = boxscore_data.get("awayTeam", {})

        # Get players from each team (already includes statistics nested in each player)
        home_players = home_team_data.get("players", [])
        away_players = away_team_data.get("players", [])

        return {
            "home": home_players if isinstance(home_players, list) else [],
            "away": away_players if isinstance(away_players, list) else [],
        }

    def _parse_api_response(self, data: dict[str, Any]) -> Sequence[DataEvent]:
        """Parse NBA API response into DataEvents."""
        from datetime import datetime, timezone

        events = []

        # Handle boxscore data (primary endpoint - replaces ScoreboardV3)
        # BoxScoreTraditionalV3 provides complete game data including all leaders
        if "boxscore" in data:
            boxscore_data = data["boxscore"]
            if not isinstance(boxscore_data, dict):
                return events

            game_id = boxscore_data.get("gameId", "")
            if not game_id:
                return events

            timestamp = datetime.now(timezone.utc)

            # Extract team data
            home_team_data = boxscore_data.get("homeTeam", {})
            away_team_data = boxscore_data.get("awayTeam", {})

            # Check if this is the initial call (no team data available yet - pre-game)
            has_team_data = bool(home_team_data and away_team_data)

            # Emit GameInitializeEvent on first call when team data is not yet available
            if not self._state.is_game_initialized(game_id) and not has_team_data:
                # Try to get game info from scoreboard API
                try:
                    from dojozero.data.nba._utils import get_game_info_by_id

                    game_info = get_game_info_by_id(game_id)
                    if game_info:
                        home_team_str = game_info.get("home_team", "")
                        away_team_str = game_info.get("away_team", "")
                        game_time_utc_str = game_info.get("game_time_utc", "")

                        # Parse game time
                        game_time_dt = timestamp  # Default to current time
                        if game_time_utc_str:
                            try:
                                from dojozero.data.nba._utils import parse_iso_datetime

                                game_time_dt = parse_iso_datetime(game_time_utc_str)
                            except (ValueError, AttributeError):
                                pass

                        if home_team_str and away_team_str:
                            events.append(
                                GameInitializeEvent(
                                    timestamp=timestamp,
                                    event_id=game_id,
                                    game_id=game_id,
                                    home_team=home_team_str,
                                    away_team=away_team_str,
                                    game_time=game_time_dt,
                                )
                            )
                            self._state.mark_game_initialized(game_id)
                except (KeyError, TypeError, ValueError, AttributeError) as e:
                    # If we can't get game info, skip GameInitializeEvent
                    # It will be emitted when boxscore data becomes available
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.debug(
                        "Could not get game info for GameInitializeEvent: game_id=%s, error=%s",
                        game_id,
                        e,
                    )

            # Only emit GameUpdateEvent if we have team data
            if has_team_data:
                # Get team statistics
                home_stats = home_team_data.get("statistics", {})
                away_stats = away_team_data.get("statistics", {})

                # Extract scores from statistics
                home_score = home_stats.get("points", 0) or 0
                away_score = away_stats.get("points", 0) or 0

                # Extract all player stats from BoxScore (pass through raw data)
                player_stats = self._extract_player_stats_from_boxscore(boxscore_data)

                # Emit GameUpdateEvent with complete BoxScore data
                # Note: Game status (start/end) is handled by GameStartEvent and GameResultEvent from PlayByPlay
                events.append(
                    GameUpdateEvent(
                        timestamp=timestamp,
                        event_id=game_id,
                        game_id=game_id,
                        period=0,  # BoxScore doesn't provide period info
                        game_clock="",  # BoxScore doesn't provide clock info
                        game_time_utc="",  # BoxScore doesn't provide game time
                        home_team={
                            "teamId": home_team_data.get("teamId", 0),
                            "teamName": home_team_data.get("teamName", ""),
                            "teamCity": home_team_data.get("teamCity", ""),
                            "teamTricode": home_team_data.get("teamTricode", ""),
                            "score": home_score,
                            "wins": 0,  # Not available in BoxScore
                            "losses": 0,  # Not available in BoxScore
                            "seed": 0,  # Not available in BoxScore
                            "timeoutsRemaining": 0,  # Not available in BoxScore
                            "inBonus": None,  # Not available in BoxScore
                            "periods": [],  # Not available in BoxScore
                        },
                        away_team={
                            "teamId": away_team_data.get("teamId", 0),
                            "teamName": away_team_data.get("teamName", ""),
                            "teamCity": away_team_data.get("teamCity", ""),
                            "teamTricode": away_team_data.get("teamTricode", ""),
                            "score": away_score,
                            "wins": 0,  # Not available in BoxScore
                            "losses": 0,  # Not available in BoxScore
                            "seed": 0,  # Not available in BoxScore
                            "timeoutsRemaining": 0,  # Not available in BoxScore
                            "inBonus": None,  # Not available in BoxScore
                            "periods": [],  # Not available in BoxScore
                        },
                        player_stats=player_stats,
                    )
                )

                # Also emit GameInitializeEvent if not already emitted (when data becomes available)
                if not self._state.is_game_initialized(game_id):
                    home_city = home_team_data.get("teamCity", "")
                    home_name = home_team_data.get("teamName", "")
                    home_team_str = (
                        f"{home_city} {home_name}".strip()
                        if (home_city or home_name)
                        else ""
                    )

                    away_city = away_team_data.get("teamCity", "")
                    away_name = away_team_data.get("teamName", "")
                    away_team_str = (
                        f"{away_city} {away_name}".strip()
                        if (away_city or away_name)
                        else ""
                    )

                    if home_team_str and away_team_str:
                        # Try to get game time from game info
                        game_time_dt = timestamp  # Default to current time
                        try:
                            from dojozero.data.nba._utils import (
                                get_game_info_by_id,
                                parse_iso_datetime,
                            )

                            game_info = get_game_info_by_id(game_id)
                            if game_info and game_info.get("game_time_utc"):
                                game_time_dt = parse_iso_datetime(
                                    game_info["game_time_utc"]
                                )
                        except (KeyError, TypeError, ValueError, AttributeError):
                            pass  # Use timestamp as fallback

                        events.append(
                            GameInitializeEvent(
                                timestamp=timestamp,
                                event_id=game_id,
                                game_id=game_id,
                                home_team=home_team_str,
                                away_team=away_team_str,
                                game_time=game_time_dt,
                            )
                        )
                        self._state.mark_game_initialized(game_id)

        # Handle play-by-play events (from NBA API PlayByPlay endpoint)
        # PlayByPlay is used for:
        # 1. Game status detection (start/end)
        # 2. All play-by-play events (no filtering - agents decide what to use)
        if "play_by_play" in data:
            play_by_play_data = data["play_by_play"]
            game_id = play_by_play_data.get("gameId", "")
            actions = play_by_play_data.get("actions", [])

            # Check if PlayByPlay just became available (game start detection)
            if game_id and not self._state.is_pbp_available(game_id) and actions:
                # First time we see actions for this game - game has started
                self._state.mark_pbp_available(game_id)
                previous_status = self._state.get_previous_status(game_id)
                if (
                    previous_status != 2
                ):  # Only emit if not already marked as in progress
                    events.append(
                        GameStartEvent(
                            timestamp=datetime.now(timezone.utc),
                            event_id=game_id,
                        )
                    )
                    self._state.set_previous_status(game_id, 2)  # In Progress

            # Check for game end (last action is "Game End")
            if actions:
                last_action = actions[-1]
                if (
                    isinstance(last_action, dict)
                    and last_action.get("actionType") == "game"
                    and "game end" in last_action.get("description", "").lower()
                ):
                    # Game has ended
                    previous_status = self._state.get_previous_status(game_id)
                    if (
                        previous_status != 3
                    ):  # Only emit if not already marked as finished
                        # Get final scores from last action
                        home_score = int(last_action.get("scoreHome", 0) or 0)
                        away_score = int(last_action.get("scoreAway", 0) or 0)
                        winner = (
                            "home"
                            if home_score > away_score
                            else "away"
                            if away_score > home_score
                            else ""
                        )

                        events.append(
                            GameResultEvent(
                                timestamp=datetime.now(timezone.utc),
                                event_id=game_id,
                                winner=winner,
                                final_score={"home": home_score, "away": away_score},
                            )
                        )
                        self._state.set_previous_status(game_id, 3)  # Finished

            # Deduplication: filter out actions we've already processed using event_id
            new_actions = self._state.filter_new_actions(game_id, actions)

            # Emit ALL play-by-play events (no filtering - let agents decide)
            for action in new_actions:
                # Parse timestamp from action
                timestamp = datetime.now(timezone.utc)
                time_actual = action.get("timeActual")
                if time_actual:
                    try:
                        from dojozero.data.nba._utils import parse_iso_datetime

                        timestamp = parse_iso_datetime(time_actual)
                    except (ValueError, AttributeError):
                        pass  # Use default timestamp

                # Extract action data
                action_type = action.get("actionType", "")
                action_number = action.get("actionNumber", 0)
                period = action.get("period", 0)
                clock = action.get("clock", "")
                person_id = action.get("personId", 0)
                player_name = action.get("playerName", "") or action.get("name", "")
                team_tricode = action.get("teamTricode", "")
                home_score = int(action.get("scoreHome", 0) or 0)
                away_score = int(action.get("scoreAway", 0) or 0)
                description = action.get("description", "")

                # Generate unique event_id for deduplication
                pbp_event_id = f"{game_id}_pbp_{action_number}"

                # Emit ALL play-by-play events
                events.append(
                    PlayByPlayEvent(
                        timestamp=timestamp,
                        event_id=pbp_event_id,
                        game_id=game_id,
                        action_type=action_type,
                        action_number=action_number,
                        period=period,
                        clock=clock,
                        person_id=person_id,
                        player_name=player_name,
                        team_tricode=team_tricode,
                        home_score=home_score,
                        away_score=away_score,
                        description=description,
                    )
                )

        return events

    async def _poll_api(
        self,
        event_type: str | None = None,
        identifier: dict[str, Any] | None = None,
    ) -> Sequence[DataEvent]:
        """Poll the API for game status, scoreboard updates, and play-by-play events."""
        if not self._api:
            return []

        events = []

        # Poll boxscore data (for complete game updates with all leaders)
        # Check if enough time has passed since last poll
        if identifier and "game_id" in identifier:
            if self._should_poll_endpoint("boxscore"):
                game_id = identifier["game_id"]
                boxscore_params = {"game_id": game_id}

                # Fetch boxscore data
                boxscore_data = await self._api.fetch("boxscore", boxscore_params)
                if boxscore_data:
                    boxscore_events = self._parse_api_response(boxscore_data)
                    events.extend(boxscore_events)
                    self._record_poll_time("boxscore")

        # Poll play-by-play events (for all PBP events and game status detection)
        # Check if enough time has passed since last poll
        if identifier and "game_id" in identifier:
            if self._should_poll_endpoint("play_by_play"):
                game_id = identifier["game_id"]
                pbp_params = {"game_id": game_id}

                # Fetch play-by-play data
                pbp_data = await self._api.fetch("play_by_play", pbp_params)
                if pbp_data:
                    pbp_events = self._parse_api_response(pbp_data)
                    events.extend(pbp_events)
                    self._record_poll_time("play_by_play")

        return events
