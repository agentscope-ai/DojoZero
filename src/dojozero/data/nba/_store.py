"""NBA data store implementation."""

from typing import Any, Sequence

import logging

from dojozero.data._models import (
    DataEvent,
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    PlayerIdentity,
    PollProfile,
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


logger = logging.getLogger(__name__)


class NBAStore(DataStore):
    """NBA data store for polling NBA API and emitting events."""

    sport_type: str = "nba"

    _POLL_PROFILES: dict[PollProfile, dict[str, float]] = {
        PollProfile.PRE_GAME: {"boxscore": 120.0, "play_by_play": 60.0},
        PollProfile.IN_GAME: {"boxscore": 30.0, "play_by_play": 10.0},
        PollProfile.LATE_GAME: {"boxscore": 15.0, "play_by_play": 5.0},
    }

    def __init__(
        self,
        store_id: str = "nba_store",
        api: ExternalAPI | None = None,
        poll_intervals: dict[str, float] | None = None,
        event_emitter=None,
    ):
        """Initialize NBA store.

        Default polling intervals (PRE_GAME profile):
        - boxscore: 120.0 seconds
        - play_by_play: 60.0 seconds

        Intervals adjust automatically based on game phase:
        - IN_GAME: boxscore=30s, play_by_play=10s
        - LATE_GAME (4Q+ and close score): boxscore=15s, play_by_play=5s
        """
        # Set default poll_intervals if not provided (PRE_GAME profile)
        if poll_intervals is None:
            poll_intervals = dict(self._POLL_PROFILES[PollProfile.PRE_GAME])

        super().__init__(
            store_id,
            api or NBAExternalAPI(),
            poll_intervals,
            event_emitter,
        )
        # Game state tracker manages all state variables in one place
        self._state = GameStateTracker()
        self._current_poll_profile: PollProfile = PollProfile.PRE_GAME

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

    @staticmethod
    def _build_player_identities(
        players: list[dict[str, Any]], sport: str
    ) -> list[PlayerIdentity]:
        """Build ``PlayerIdentity`` list from boxscore player dicts."""
        result: list[PlayerIdentity] = []
        for p in players:
            pid = str(p.get("personId", ""))
            result.append(
                PlayerIdentity(
                    player_id=pid,
                    name=p.get("name", ""),
                    position=p.get("position", ""),
                    jersey=p.get("jersey", ""),
                    headshot_url=(
                        f"https://a.espncdn.com/i/headshots/{sport}/players/full/{pid}.png"
                        if pid
                        else ""
                    ),
                )
            )
        return result

    async def _enrich_boxscore_rosters(self, boxscore_data: dict[str, Any]) -> None:
        """Fetch team rosters and inject into boxscore when players are missing.

        Pre-game boxscores have team info but no player data.  This method
        fetches rosters from the ESPN team_roster endpoint and merges them
        into the boxscore dict so that ``_parse_api_response`` can build
        ``PlayerIdentity`` lists for ``GameInitializeEvent``.
        """
        bs = boxscore_data.get("boxscore", {})
        if not bs:
            return

        for side in ("homeTeam", "awayTeam"):
            team_data = bs.get(side, {})
            if not team_data or team_data.get("players"):
                continue  # Already has players

            team_id = str(team_data.get("teamId", ""))
            if not team_id or not isinstance(self._api, NBAExternalAPI):
                continue

            try:
                # Access the underlying ESPN API for team_roster
                result = await self._api._api.fetch("team_roster", {"team_id": team_id})
                athletes = result.get("team_roster", {}).get("athletes", [])
                players: list[dict[str, Any]] = []
                for a in athletes:
                    if not isinstance(a, dict):
                        continue
                    pos = a.get("position", {})
                    players.append(
                        {
                            "personId": a.get("id", 0),
                            "name": a.get("displayName", ""),
                            "position": pos.get("abbreviation", "")
                            if isinstance(pos, dict)
                            else "",
                            "jersey": a.get("jersey", ""),
                        }
                    )
                team_data["players"] = players
            except Exception:
                logger.debug(
                    "Failed to fetch roster for team %s", team_id, exc_info=True
                )

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

            # Build team/player lookup maps for PBP enrichment
            if has_team_data:
                home_tid = str(home_team_data.get("teamId", ""))
                away_tid = str(away_team_data.get("teamId", ""))
                if home_tid and away_tid:
                    self._state.set_team_ids(game_id, home_tid, away_tid)
                for team_data in (home_team_data, away_team_data):
                    tid = str(team_data.get("teamId", ""))
                    tri = team_data.get("teamTricode", "")
                    city = team_data.get("teamCity", "")
                    tname = team_data.get("teamName", "")
                    display_name = f"{city} {tname}".strip()
                    self._state.update_team_lookup(tid, tri, display_name)
                    for player in team_data.get("players", []):
                        if isinstance(player, dict):
                            pid = player.get("personId", 0)
                            pname = player.get("name", "")
                            self._state.update_player_lookup(int(pid), pname)

                # Extract starters from boxscore (starter=True per player)
                home_starters = [
                    p
                    for p in home_team_data.get("players", [])
                    if isinstance(p, dict) and p.get("starter")
                ]
                away_starters = [
                    p
                    for p in away_team_data.get("players", [])
                    if isinstance(p, dict) and p.get("starter")
                ]
                if home_starters or away_starters:
                    self._state.set_starters(game_id, home_starters, away_starters)

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
                                        timezone=game_info.venue.timezone,
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
                    logger.debug(
                        "Could not get game info for GameInitializeEvent: game_id=%s, error=%s",
                        game_id,
                        e,
                    )

            # Check for game conclusion from boxscore status (STATUS_FINAL)
            # This allows detecting game end even before PBP processes the "End Game" action
            status_data = boxscore_data.get("status", {})
            status_type = status_data.get("statusType", "")
            if status_type == "STATUS_FINAL":
                # Mark game as concluded in state tracker if not already
                if not self._state.is_game_concluded(game_id):
                    self._state.set_previous_status(game_id, 3)  # STATUS_FINAL

            # Skip emitting game updates if game is concluded and we've already emitted the final update
            game_concluded = self._state.is_game_concluded(game_id)
            final_update_emitted = self._state.has_final_update_emitted(game_id)
            should_emit_update = not (game_concluded and final_update_emitted)

            # Only emit GameUpdateEvent if we have team data and game hasn't already concluded
            if has_team_data and should_emit_update:
                # Get team statistics
                home_stats = home_team_data.get("statistics", {})
                away_stats = away_team_data.get("statistics", {})

                # Extract scores from statistics
                home_score = home_stats.get("points", 0) or 0
                away_score = away_stats.get("points", 0) or 0

                # Track scores for poll profile calculation
                self._state.update_scores(game_id, home_score, away_score)

                # Extract all player stats from BoxScore (pass through raw data)
                player_stats = self._extract_player_stats_from_boxscore(boxscore_data)

                # Get latest period/clock from play-by-play state tracker.
                # The state tracker only stores valid values (period > 0), so this
                # returns the last valid period/clock even if API returns invalid data.
                # For concluded games with no PBP data yet, default to period 4
                # (NBA regulation). OT games will be corrected when PBP is processed.
                period = self._state.get_current_period(game_id)
                game_clock = self._state.get_current_clock(game_id)
                if not period and game_concluded:
                    period = 4  # NBA regulation
                    game_clock = "0:00"

                # Skip emission if period is still 0 after fallback and game had PBP
                # tracking active. This prevents post-game garbage data from being emitted.
                # Note: If PBP was never available, period=0 is expected (pre-game or boxscore-only).
                if not period and self._state.is_pbp_available(game_id):
                    logger.debug(
                        "Skipping game update with invalid period=0 for game %s "
                        "(PBP was available, scores: %d-%d)",
                        game_id,
                        home_score,
                        away_score,
                    )
                else:
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
                    # Mark final update as emitted if game is concluded
                    if game_concluded:
                        self._state.mark_final_update_emitted(game_id)

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
                                    timezone=game_info.venue.timezone,
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

                        home_players = self._build_player_identities(
                            home_team_data.get("players", []), "nba"
                        )
                        away_players = self._build_player_identities(
                            away_team_data.get("players", []), "nba"
                        )

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
                                    players=home_players,
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
                                    players=away_players,
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
                            home_starters=self._build_player_identities(
                                self._state.get_home_starters(game_id), "nba"
                            ),
                            away_starters=self._build_player_identities(
                                self._state.get_away_starters(game_id), "nba"
                            ),
                        )
                    )
                    self._state.set_previous_status(game_id, 2)  # In Progress

            # Detect game end (last action is "Game End" or "End of Game")
            # We detect here but emit GameResultEvent AFTER play events
            # so that plays are always ordered before the result.
            game_ended = False
            if actions:
                last_action = actions[-1]
                if (
                    isinstance(last_action, dict)
                    and last_action.get("actionType") in ("game", "end game")
                    and "end" in last_action.get("description", "").lower()
                    and "game" in last_action.get("description", "").lower()
                ):
                    previous_status = self._state.get_previous_status(game_id)
                    if previous_status != 3:
                        game_ended = True

            # Deduplication: filter out actions we've already processed using event_id
            new_actions = self._state.filter_new_actions(game_id, actions)

            # Emit ALL play-by-play events (no filtering - let agents decide)
            timestamp = datetime.now(timezone.utc)
            for action in new_actions:
                # Parse wallclock time from action (when the play actually happened)
                game_timestamp: datetime | None = None
                time_actual = action.get("timeActual")
                if time_actual:
                    try:
                        from dojozero.data.nba._utils import parse_iso_datetime

                        game_timestamp = parse_iso_datetime(time_actual)
                    except (ValueError, AttributeError):
                        pass

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

                # Enrich from boxscore lookup maps when ESPN PBP
                # only provides numeric IDs without names/tricodes
                if team_id and not team_tricode:
                    team_tricode = self._state.get_team_tricode(team_id)
                if person_id and not player_name:
                    player_name = self._state.get_player_name(int(person_id))
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
                        game_timestamp=game_timestamp,
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

            # Emit GameResultEvent AFTER all play events for correct ordering
            if game_ended:
                home_score = int(last_action.get("scoreHome", 0) or 0)
                away_score = int(last_action.get("scoreAway", 0) or 0)
                winner = (
                    "home"
                    if home_score > away_score
                    else "away"
                    if away_score > home_score
                    else ""
                )
                home_tid = self._state.get_home_team_id(game_id)
                away_tid = self._state.get_away_team_id(game_id)
                events.append(
                    GameResultEvent(
                        timestamp=datetime.now(timezone.utc),
                        game_id=game_id,
                        sport="nba",
                        winner=winner,
                        home_score=home_score,
                        away_score=away_score,
                        home_team_name=self._state.get_team_name(home_tid),
                        away_team_name=self._state.get_team_name(away_tid),
                        home_team_id=home_tid,
                        away_team_id=away_tid,
                    )
                )
                self._state.set_previous_status(game_id, 3)  # Finished

        # Check if poll profile needs to change
        new_profile = self._state.get_poll_profile(game_id)
        if new_profile != self._current_poll_profile:
            intervals = self._POLL_PROFILES.get(new_profile)
            if intervals:
                for endpoint, interval in intervals.items():
                    self.update_poll_interval(endpoint, interval)
            logger.info(
                "Poll profile changed: %s -> %s for game %s",
                self._current_poll_profile.value,
                new_profile.value,
                game_id,
            )
            self._current_poll_profile = new_profile

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

        # Poll boxscore data first (for game_initialize and game updates)
        if identifier and "espn_game_id" in identifier:
            if self._should_poll_endpoint("boxscore"):
                espn_game_id = identifier["espn_game_id"]
                boxscore_params = {"game_id": espn_game_id}

                # Fetch boxscore data
                boxscore_data = await self._api.fetch("boxscore", boxscore_params)
                if boxscore_data:
                    # Pre-game: boxscore has teams but no players.
                    # Fetch rosters so GameInitializeEvent carries full lineups.
                    if not self._state.is_game_initialized(
                        boxscore_data.get("boxscore", {}).get("gameId", "")
                    ):
                        await self._enrich_boxscore_rosters(boxscore_data)
                    boxscore_events = self._parse_api_response(boxscore_data)
                    events.extend(boxscore_events)
                    self._record_poll_time("boxscore")

        # Poll play-by-play events (for all PBP events and game status detection)
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
