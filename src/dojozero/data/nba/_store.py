"""NBA data store implementation."""

from typing import Any, Sequence

from dojozero.data._models import (
    DataEvent,
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    TeamIdentity,
    VenueInfo,
)
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data.nba._api import NBAExternalAPI
from dojozero.data.nba._events import (
    NBAGamePlayerStats,
    NBAGameUpdateEvent,
    NBAPlayEvent,
    NBAPlayerStats,
    NBATeamGameStats,
)
from dojozero.data.nba._state_tracker import GameStateTracker


class NBAStore(DataStore):
    """NBA data store for polling NBA API and emitting events."""

    sport_type: str = "nba"

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
                # Try to get game info from ESPN summary API
                try:
                    from dojozero.data.nba._utils import get_game_info_by_id

                    game_date = self._poll_identifier.get("game_date")
                    game_info = get_game_info_by_id(game_id, game_date=game_date)
                    if game_info:
                        home = game_info.home_team
                        away = game_info.away_team

                        if home.name and away.name:
                            events.append(
                                GameInitializeEvent(
                                    timestamp=timestamp,
                                    game_id=game_id,
                                    sport="nba",
                                    home_team=TeamIdentity(
                                        team_id=home.team_id,
                                        name=home.name,
                                        tricode=home.tricode,
                                        location=home.location,
                                        color=home.color,
                                        alternate_color=home.alternate_color,
                                        logo_url=home.logo,
                                        record=home.record,
                                    ),
                                    away_team=TeamIdentity(
                                        team_id=away.team_id,
                                        name=away.name,
                                        tricode=away.tricode,
                                        location=away.location,
                                        color=away.color,
                                        alternate_color=away.alternate_color,
                                        logo_url=away.logo,
                                        record=away.record,
                                    ),
                                    venue=VenueInfo(
                                        venue_id=game_info.venue.venue_id,
                                        name=game_info.venue.name,
                                        city=game_info.venue.city,
                                        state=game_info.venue.state,
                                        indoor=game_info.venue.indoor,
                                    ),
                                    game_time=game_info.game_time_utc or timestamp,
                                    broadcast=game_info.broadcast,
                                    season_year=game_info.season_year,
                                    season_type=game_info.season_type,
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

                # Get latest period/clock from play-by-play state tracker
                # (boxscore endpoint doesn't carry period/clock reliably)
                period = self._state.get_current_period(game_id)
                game_clock = self._state.get_current_clock(game_id)
                # Game time from header status (if available)
                status_data = boxscore_data.get("status", {})
                game_time_utc = status_data.get("date", "") or ""

                # Emit GameUpdateEvent with complete BoxScore data
                # Note: Game status (start/end) is handled by GameStartEvent and GameResultEvent from PlayByPlay
                events.append(
                    NBAGameUpdateEvent(
                        timestamp=timestamp,
                        game_id=game_id,
                        sport="nba",
                        period=period,
                        game_clock=game_clock,
                        game_time_utc=game_time_utc,
                        home_score=home_score,
                        away_score=away_score,
                        home_team_stats=NBATeamGameStats(
                            team_id=home_team_data.get("teamId", 0),
                            team_name=home_team_data.get("teamName", ""),
                            team_city=home_team_data.get("teamCity", ""),
                            team_tricode=home_team_data.get("teamTricode", ""),
                            score=home_score,
                        ),
                        away_team_stats=NBATeamGameStats(
                            team_id=away_team_data.get("teamId", 0),
                            team_name=away_team_data.get("teamName", ""),
                            team_city=away_team_data.get("teamCity", ""),
                            team_tricode=away_team_data.get("teamTricode", ""),
                            score=away_score,
                        ),
                        player_stats=NBAGamePlayerStats(
                            home=[
                                NBAPlayerStats(
                                    player_id=p.get("personId", 0),
                                    name=p.get("name", ""),
                                    position=p.get("position", ""),
                                    statistics=p.get("statistics", {}),
                                )
                                for p in player_stats.get("home", [])
                            ],
                            away=[
                                NBAPlayerStats(
                                    player_id=p.get("personId", 0),
                                    name=p.get("name", ""),
                                    position=p.get("position", ""),
                                    statistics=p.get("statistics", {}),
                                )
                                for p in player_stats.get("away", [])
                            ],
                        ),
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
                        # Get enriched game info (venue, broadcast, season, record)
                        game_time_dt = timestamp
                        venue = VenueInfo()
                        broadcast = ""
                        season_year = 0
                        season_type = ""
                        home_record = ""
                        away_record = ""
                        home_color = ""
                        home_alt_color = ""
                        home_logo = ""
                        away_color = ""
                        away_alt_color = ""
                        away_logo = ""

                        try:
                            from dojozero.data.nba._utils import get_game_info_by_id

                            game_date = self._poll_identifier.get("game_date")
                            game_info = get_game_info_by_id(
                                game_id, game_date=game_date
                            )
                            if game_info:
                                if game_info.game_time_utc:
                                    game_time_dt = game_info.game_time_utc
                                venue = VenueInfo(
                                    venue_id=game_info.venue.venue_id,
                                    name=game_info.venue.name,
                                    city=game_info.venue.city,
                                    state=game_info.venue.state,
                                    indoor=game_info.venue.indoor,
                                )
                                broadcast = game_info.broadcast
                                season_year = game_info.season_year
                                season_type = game_info.season_type
                                home_record = game_info.home_team.record
                                away_record = game_info.away_team.record
                                home_color = game_info.home_team.color
                                home_alt_color = game_info.home_team.alternate_color
                                home_logo = game_info.home_team.logo
                                away_color = game_info.away_team.color
                                away_alt_color = game_info.away_team.alternate_color
                                away_logo = game_info.away_team.logo
                        except (KeyError, TypeError, ValueError, AttributeError):
                            pass  # Use defaults

                        events.append(
                            GameInitializeEvent(
                                timestamp=timestamp,
                                game_id=game_id,
                                sport="nba",
                                home_team=TeamIdentity(
                                    team_id=str(home_team_data.get("teamId", "")),
                                    name=home_team_str,
                                    tricode=home_team_data.get("teamTricode", ""),
                                    location=home_city,
                                    color=home_color,
                                    alternate_color=home_alt_color,
                                    logo_url=home_logo,
                                    record=home_record,
                                ),
                                away_team=TeamIdentity(
                                    team_id=str(away_team_data.get("teamId", "")),
                                    name=away_team_str,
                                    tricode=away_team_data.get("teamTricode", ""),
                                    location=away_city,
                                    color=away_color,
                                    alternate_color=away_alt_color,
                                    logo_url=away_logo,
                                    record=away_record,
                                ),
                                venue=venue,
                                game_time=game_time_dt,
                                broadcast=broadcast,
                                season_year=season_year,
                                season_type=season_type,
                            )
                        )
                        self._state.mark_game_initialized(game_id)

        # Handle play-by-play events (from NBA API PlayByPlay endpoint)
        # PlayByPlay is used for:
        # 1. Game status detection (start/end)
        # 2. All play-by-play events (no filtering - agents decide what to use)
        if "play_by_play" in data:
            play_by_play_data = data["play_by_play"]
            if not isinstance(play_by_play_data, dict):
                return events

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
                            game_id=game_id,
                            sport="nba",
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
                                game_id=game_id,
                                sport="nba",
                                winner=winner,
                                home_score=home_score,
                                away_score=away_score,
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
                # Track latest period/clock for boxscore updates
                if period:
                    self._state.update_game_clock(game_id, period, clock)
                person_id = action.get("personId", 0)
                player_name = action.get("playerName", "") or action.get("name", "")
                team_id = str(action.get("teamId", ""))
                team_tricode = action.get("teamTricode", "")
                home_score = int(action.get("scoreHome", 0) or 0)
                away_score = int(action.get("scoreAway", 0) or 0)
                description = action.get("description", "")
                is_scoring_play = bool(action.get("scoringPlay", False))
                score_value = int(action.get("scoreValue", 0) or 0)
                play_id = action.get("playId", "")

                # Generate unique event_id for deduplication
                pbp_event_id = f"{game_id}_pbp_{action_number}"

                # Emit ALL play-by-play events
                events.append(
                    NBAPlayEvent(
                        timestamp=timestamp,
                        game_id=game_id,
                        sport="nba",
                        event_id=pbp_event_id,
                        action_type=action_type,
                        action_number=action_number,
                        period=period,
                        clock=clock,
                        player_id=person_id,
                        player_name=player_name,
                        team_id=team_id,
                        team_tricode=team_tricode,
                        home_score=home_score,
                        away_score=away_score,
                        description=description,
                        play_id=play_id,
                        is_scoring_play=is_scoring_play,
                        score_value=score_value,
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
        if identifier and "espn_game_id" in identifier:
            if self._should_poll_endpoint("boxscore"):
                espn_game_id = identifier["espn_game_id"]
                boxscore_params = {"game_id": espn_game_id}

                # Fetch boxscore data
                boxscore_data = await self._api.fetch("boxscore", boxscore_params)
                if boxscore_data:
                    boxscore_events = self._parse_api_response(boxscore_data)
                    events.extend(boxscore_events)
                    self._record_poll_time("boxscore")

        # Poll play-by-play events (for all PBP events and game status detection)
        # Check if enough time has passed since last poll
        if identifier and "espn_game_id" in identifier:
            if self._should_poll_endpoint("play_by_play"):
                espn_game_id = identifier["espn_game_id"]
                pbp_params = {"game_id": espn_game_id}

                # Fetch play-by-play data
                pbp_data = await self._api.fetch("play_by_play", pbp_params)
                if pbp_data:
                    pbp_events = self._parse_api_response(pbp_data)
                    events.extend(pbp_events)
                    self._record_poll_time("play_by_play")

        return events
