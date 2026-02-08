"""NFL data store implementation."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from dojozero.data._models import (
    DataEvent,
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    PlayerIdentity,
    PollProfile,
)
from dojozero.data._models import (
    TeamIdentity,
    VenueInfo,
    get_timezone_for_state,
)
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data.nfl._api import NFLExternalAPI
from dojozero.data.nfl._events import (
    NFLDriveEvent,
    NFLGameUpdateEvent,
    NFLPlayEvent,
    NFLTeamGameStats,
)
from dojozero.data.nfl._state_tracker import NFLGameStateTracker

logger = logging.getLogger(__name__)


class NFLStore(DataStore):
    """NFL data store for polling ESPN API and emitting events."""

    sport_type: str = "nfl"

    _POLL_PROFILES: dict[PollProfile, dict[str, float]] = {
        PollProfile.PRE_GAME: {"scoreboard": 120.0, "summary": 60.0, "plays": 30.0},
        PollProfile.IN_GAME: {"scoreboard": 60.0, "summary": 15.0, "plays": 10.0},
        PollProfile.LATE_GAME: {"scoreboard": 30.0, "summary": 10.0, "plays": 5.0},
    }

    def __init__(
        self,
        store_id: str = "nfl_store",
        api: ExternalAPI | None = None,
        poll_intervals: dict[str, float] | None = None,
        event_emitter=None,
    ):
        """Initialize NFL store.

        Default polling intervals (PRE_GAME profile):
        - scoreboard: 120.0 seconds
        - summary: 60.0 seconds
        - plays: 30.0 seconds

        Intervals adjust automatically based on game phase:
        - IN_GAME: scoreboard=60s, summary=15s, plays=10s
        - LATE_GAME (4Q+ and close score): scoreboard=30s, summary=10s, plays=5s
        """
        if poll_intervals is None:
            poll_intervals = dict(self._POLL_PROFILES[PollProfile.PRE_GAME])

        super().__init__(
            store_id,
            api or NFLExternalAPI(),
            poll_intervals,
            event_emitter,
        )
        self._state = NFLGameStateTracker()
        self._current_poll_profile: PollProfile = PollProfile.PRE_GAME
        # Cache: team_id -> list[PlayerIdentity]
        self._roster_cache: dict[str, list[PlayerIdentity]] = {}
        # Game start time for computing game_timestamp on events
        self._game_start_time: datetime | None = None

    # Real-time multiplier: an NFL quarter has 15 min of game clock but takes
    # ~45 min of real time (stoppages, play clock, commercials, reviews).
    # A typical game is ~3h15m (195 min) for 60 min of game clock → 3.25x.
    _GAME_CLOCK_MULTIPLIER: float = 3.25

    # Real-time quarter offsets in seconds from game start.
    # Each quarter ≈ 900s * 3.25 = 2925s real time.
    # Halftime ≈ 1200s (20 min) real time (not multiplied).
    _QUARTER_OFFSETS: dict[int, int] = {
        1: 0,  # Q1 start
        2: 2925,  # Q2 start: 1 quarter
        3: 7050,  # Q3 start: 2 quarters (5850) + halftime (1200)
        4: 9975,  # Q4 start: 3 quarters (8775) + halftime (1200)
        5: 12900,  # OT start: 4 quarters (11700) + halftime (1200)
    }

    def _compute_game_timestamp(self, period: int, clock: str) -> datetime | None:
        """Compute approximate wallclock time from game clock.

        Maps game clock (period + countdown) to real elapsed time using a
        multiplier that accounts for stoppages, commercials, and play clock.
        A 15-min quarter takes ~45 min of real time (3.25x multiplier).

        Args:
            period: Quarter number (1-4, 5 for OT)
            clock: Game clock countdown string (e.g., "12:34")

        Returns:
            Approximate datetime when the event occurred, or None if
            game start time is unknown or inputs are invalid.
        """
        if not self._game_start_time or period < 1:
            return None

        # Parse clock "MM:SS" → seconds remaining in quarter
        remaining = 900  # default to start of quarter
        if clock:
            try:
                parts = clock.split(":")
                if len(parts) == 2:
                    remaining = int(parts[0]) * 60 + int(parts[1])
            except (ValueError, TypeError):
                pass

        offset = self._QUARTER_OFFSETS.get(period, 12900)
        # Elapsed within the quarter: game clock consumed × real-time multiplier
        elapsed_in_quarter = (900 - remaining) * self._GAME_CLOCK_MULTIPLIER
        return self._game_start_time + timedelta(seconds=offset + elapsed_in_quarter)

    def _parse_api_response(
        self, data: dict[str, Any], target_event_id: str | None = None
    ) -> Sequence[DataEvent]:
        """Parse ESPN API response into DataEvents.

        Args:
            data: API response data
            target_event_id: If provided, only emit events for this game ID.
                           If None, emit events for all games (used for multi-game monitoring).
        """
        events: list[DataEvent] = []
        timestamp = datetime.now(timezone.utc)

        # Handle scoreboard response
        if "scoreboard" in data:
            scoreboard_events = self._parse_scoreboard(
                data["scoreboard"], timestamp, target_event_id
            )
            events.extend(scoreboard_events)

        # Handle summary response
        if "summary" in data:
            summary_events = self._parse_summary(data["summary"], timestamp)
            events.extend(summary_events)

        # Handle plays response
        if "plays" in data:
            plays_events = self._parse_plays(data["plays"], timestamp)
            events.extend(plays_events)

        # Check if poll profile needs to change
        espn_game_id = (self._poll_identifier or {}).get("espn_game_id", "")
        if espn_game_id:
            new_profile = self._state.get_poll_profile(espn_game_id)
            if new_profile != self._current_poll_profile:
                intervals = self._POLL_PROFILES.get(new_profile)
                if intervals:
                    for endpoint, interval in intervals.items():
                        self.update_poll_interval(endpoint, interval)
                logger.info(
                    "Poll profile changed: %s -> %s for game %s",
                    self._current_poll_profile.value,
                    new_profile.value,
                    espn_game_id,
                )
                self._current_poll_profile = new_profile

        return events

    def _parse_scoreboard(
        self,
        data: dict[str, Any],
        timestamp: datetime,
        target_event_id: str | None = None,
    ) -> list[DataEvent]:
        """Parse scoreboard data into events.

        Args:
            data: Scoreboard API response data
            timestamp: Timestamp to use for events
            target_event_id: If provided, only emit events for this game ID.
                           If None, emit events for all games.

        Emits:
        - GameInitializeEvent for new games
        - OddsUpdateEvent when odds change
        - GameStartEvent when game status changes to in_progress
        - GameResultEvent when game status changes to final
        """
        events: list[DataEvent] = []

        scoreboard_events = data.get("events", []) or []
        for game in scoreboard_events:
            if not game or not isinstance(game, dict):
                continue

            event_id = str(game.get("id", ""))
            if not event_id:
                continue

            # Filter to target game if specified
            if target_event_id and event_id != target_event_id:
                continue

            # Get competition data
            competitions = game.get("competitions", []) or []
            if not competitions or not isinstance(competitions[0], dict):
                continue
            competition = competitions[0]

            # Get competitors (teams)
            competitors = competition.get("competitors", []) or []
            if len(competitors) < 2:
                continue

            # Identify home and away teams
            home_team_data = None
            away_team_data = None
            for comp in competitors:
                if not comp or not isinstance(comp, dict):
                    continue
                if comp.get("homeAway") == "home":
                    home_team_data = comp
                elif comp.get("homeAway") == "away":
                    away_team_data = comp

            if not home_team_data or not away_team_data:
                continue

            home_team_info = home_team_data.get("team", {}) or {}
            away_team_info = away_team_data.get("team", {}) or {}

            # Emit GameInitializeEvent for new games
            if not self._state.is_game_initialized(event_id):
                # Parse game time
                game_time_str = game.get("date", "")
                game_time = timestamp
                if game_time_str:
                    try:
                        game_time = datetime.fromisoformat(
                            game_time_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                # Get venue details
                venue_data = competition.get("venue", {})
                venue_address = venue_data.get("address", {})

                # Extract team records
                home_records = home_team_data.get("records", [])
                home_record = home_records[0].get("summary", "") if home_records else ""
                away_records = away_team_data.get("records", [])
                away_record = away_records[0].get("summary", "") if away_records else ""

                # Extract broadcast info
                broadcasts_raw = competition.get("broadcasts", [])
                broadcast_names: list[str] = []
                for b in broadcasts_raw:
                    names = b.get("names", [])
                    if names:
                        broadcast_names.extend(names)
                broadcast = ", ".join(broadcast_names)

                # Get season info
                season = game.get("season", {})
                season_type_code = (
                    season.get("type", 2) if isinstance(season, dict) else 2
                )
                season_type_map = {1: "preseason", 2: "regular", 3: "postseason"}
                season_type = season_type_map.get(season_type_code, "regular")
                season_year = season.get("year", 0) if isinstance(season, dict) else 0

                home_team_id = str(home_team_info.get("id", ""))
                away_team_id = str(away_team_info.get("id", ""))

                # Store game start time for computing game_timestamp
                self._game_start_time = game_time

                events.append(
                    GameInitializeEvent(
                        timestamp=timestamp,
                        game_timestamp=game_time,
                        game_id=event_id,
                        sport="nfl",
                        home_team=TeamIdentity(
                            team_id=home_team_id,
                            name=home_team_info.get("displayName", ""),
                            tricode=home_team_info.get("abbreviation", ""),
                            location=home_team_info.get("location", ""),
                            color=home_team_info.get("color", ""),
                            alternate_color=home_team_info.get("alternateColor", ""),
                            logo_url=home_team_info.get("logo", ""),
                            record=home_record,
                            players=self._roster_cache.get(home_team_id, []),
                        ),
                        away_team=TeamIdentity(
                            team_id=away_team_id,
                            name=away_team_info.get("displayName", ""),
                            tricode=away_team_info.get("abbreviation", ""),
                            location=away_team_info.get("location", ""),
                            color=away_team_info.get("color", ""),
                            alternate_color=away_team_info.get("alternateColor", ""),
                            logo_url=away_team_info.get("logo", ""),
                            record=away_record,
                            players=self._roster_cache.get(away_team_id, []),
                        ),
                        venue=VenueInfo(
                            venue_id=str(venue_data.get("id", "")),
                            name=venue_data.get("fullName", ""),
                            city=venue_address.get("city", ""),
                            state=venue_address.get("state", ""),
                            indoor=venue_data.get("indoor", True),
                            timezone=get_timezone_for_state(
                                venue_address.get("state", "")
                            ),
                        ),
                        game_time=game_time,
                        broadcast=broadcast,
                        season_year=season_year,
                        season_type=season_type,
                    )
                )
                self._state.mark_game_initialized(event_id)

            # Note: ESPN odds from DraftKings/FanDuel are ignored.
            # We rely solely on Polymarket odds via PolymarketStore.

            # Handle game status transitions
            status_data = competition.get("status", {}) or {}
            status = status_data.get("type", {}) or {}
            status_name = status.get("name", "")
            status_code = self._status_name_to_code(status_name)
            previous_status = self._state.get_previous_status(event_id)

            if status_code != previous_status:
                # Game started
                if (
                    status_code == NFLGameStateTracker.STATUS_IN_PROGRESS
                    and previous_status != NFLGameStateTracker.STATUS_IN_PROGRESS
                ):
                    # Starters will be populated asynchronously via
                    # _fetch_game_starters() called from _poll_api; store
                    # them in state tracker and include here if available.
                    home_team_id = str(home_team_info.get("id", ""))
                    away_team_id = str(away_team_info.get("id", ""))
                    home_starters = self._state.get_starters(event_id, home_team_id)
                    away_starters = self._state.get_starters(event_id, away_team_id)
                    events.append(
                        GameStartEvent(
                            timestamp=timestamp,
                            game_timestamp=self._game_start_time,
                            game_id=event_id,
                            sport="nfl",
                            home_starters=home_starters,
                            away_starters=away_starters,
                        )
                    )

                # Game ended
                if status_code == NFLGameStateTracker.STATUS_FINAL:
                    home_score = int(home_team_data.get("score", 0) or 0)
                    away_score = int(away_team_data.get("score", 0) or 0)
                    winner = (
                        "home"
                        if home_score > away_score
                        else "away"
                        if away_score > home_score
                        else ""
                    )

                    # Compute game_timestamp from final period/clock
                    final_period = int(status_data.get("period", 4) or 4)
                    final_clock = status_data.get("displayClock", "0:00") or "0:00"
                    events.append(
                        GameResultEvent(
                            timestamp=timestamp,
                            game_timestamp=self._compute_game_timestamp(
                                final_period, final_clock
                            ),
                            game_id=event_id,
                            sport="nfl",
                            winner=winner,
                            home_score=home_score,
                            away_score=away_score,
                            home_team_name=home_team_info.get("displayName", ""),
                            away_team_name=away_team_info.get("displayName", ""),
                            home_team_id=str(home_team_info.get("id", "")),
                            away_team_id=str(away_team_info.get("id", "")),
                        )
                    )

                self._state.set_previous_status(event_id, status_code)

        return events

    def _parse_summary(
        self, data: dict[str, Any], timestamp: datetime
    ) -> list[DataEvent]:
        """Parse game summary data into events.

        Emits:
        - NFLGameUpdateEvent with boxscore data (skipped if game concluded and final update already emitted)
        - NFLDriveEvent for completed drives
        """
        events: list[DataEvent] = []

        event_id = str(data.get("eventId", ""))
        if not event_id:
            return events

        # Skip emitting game updates if game is concluded and we've already emitted the final update
        game_concluded = self._state.is_game_concluded(event_id)
        final_update_emitted = self._state.has_final_update_emitted(event_id)
        should_emit_update = not (game_concluded and final_update_emitted)

        # Parse boxscore
        boxscore = data.get("boxscore", {}) or {}
        if boxscore:
            teams = boxscore.get("teams", []) or []
            if len(teams) >= 2:
                # Get header info for current game state
                header = data.get("header", {}) or {}
                competitions = header.get("competitions", []) or []
                competition = (
                    competitions[0]
                    if competitions and isinstance(competitions[0], dict)
                    else {}
                )
                status = competition.get("status", {}) or {}

                # Extract game state from status
                quarter = int(status.get("period", 0) or 0)
                game_clock = status.get("displayClock", "") or ""

                # Track valid period/clock in state tracker (only updates if period > 0)
                # This preserves the last valid state for use as fallback
                self._state.update_game_clock(event_id, quarter, game_clock)

                # If API returned invalid period (0), try to use last valid values
                if not quarter:
                    last_valid_period = self._state.get_last_valid_period(event_id)
                    if last_valid_period > 0:
                        quarter = last_valid_period
                        game_clock = self._state.get_last_valid_clock(event_id)
                    elif game_concluded:
                        # Fallback for concluded games with no PBP history
                        quarter = 4  # NFL regulation
                        game_clock = "0:00"

                # Get possession and down info from situation
                situation = data.get("situation", {}) or {}
                possession_team = situation.get("possession", "")
                down = int(situation.get("down", 0) or 0)
                distance = int(situation.get("distance", 0) or 0)
                yard_line_raw = situation.get("yardLine", "")

                # Convert yard_line to 0-100 int
                # API may return integer (26) or string "KC 25" format
                # 0 = home goal line, 100 = away goal line
                yard_line = 0
                if isinstance(yard_line_raw, int):
                    # Already an integer (PBP-style format)
                    yard_line = yard_line_raw
                elif yard_line_raw:
                    yard_line_str = str(yard_line_raw)
                    if " " in yard_line_str:
                        # "KC 25" format - parse team and yards
                        try:
                            parts = yard_line_str.split()
                            yl_team = parts[0]
                            yl_yards = int(parts[1])
                            # Get home team abbreviation
                            home_abbrev = ""
                            for team in teams:
                                if team and team.get("homeAway") == "home":
                                    team_info = team.get("team", {}) or {}
                                    home_abbrev = team_info.get("abbreviation", "")
                                    break
                            # If yard_line team is home team, it's in home territory (0-50)
                            # If yard_line team is away team, it's in away territory (50-100)
                            if yl_team == home_abbrev:
                                yard_line = yl_yards
                            else:
                                yard_line = 100 - yl_yards
                        except (ValueError, IndexError):
                            yard_line = 0
                    else:
                        # Plain numeric string
                        try:
                            yard_line = int(yard_line_str)
                        except ValueError:
                            yard_line = 0

                # Get line scores from header competitors
                home_line_scores: list[int] = []
                away_line_scores: list[int] = []
                competitors = competition.get("competitors", []) or []
                for comp in competitors:
                    if not comp or not isinstance(comp, dict):
                        continue
                    line_scores = comp.get("linescores", []) or []
                    scores = [
                        int(ls.get("value", 0) or ls.get("displayValue", 0) or 0)
                        for ls in line_scores
                        if ls and isinstance(ls, dict)
                    ]
                    if comp.get("homeAway") == "home":
                        home_line_scores = scores
                    else:
                        away_line_scores = scores

                # Build team stats dicts
                home_team_dict: dict[str, Any] = {}
                away_team_dict: dict[str, Any] = {}
                for team in teams:
                    if not team or not isinstance(team, dict):
                        continue
                    team_info = team.get("team", {}) or {}
                    home_away = team.get("homeAway", "")
                    team_dict = {
                        "team": team_info,
                        "statistics": team.get("statistics", []),
                        "score": 0,  # Will be filled from competitors
                    }
                    if home_away == "home":
                        home_team_dict = team_dict
                    else:
                        away_team_dict = team_dict

                # Get scores from competitors
                for comp in competitors:
                    if not comp or not isinstance(comp, dict):
                        continue
                    score = int(comp.get("score", 0) or 0)
                    if comp.get("homeAway") == "home":
                        home_team_dict["score"] = score
                    else:
                        away_team_dict["score"] = score

                # Track period and scores for poll profile calculation
                home_score = home_team_dict.get("score", 0) if home_team_dict else 0
                away_score = away_team_dict.get("score", 0) if away_team_dict else 0
                self._state.update_game_state(event_id, quarter, home_score, away_score)

                # Only emit update if we should (skip duplicates for concluded games)
                if should_emit_update:
                    # Skip emission if period is still 0 after fallback and game had started.
                    # This prevents post-game garbage data from being emitted.
                    # Note: If game hasn't started, period=0 is expected (pre-game state).
                    if not quarter and self._state.has_game_started(event_id):
                        logger.debug(
                            "Skipping game update with invalid period=0 for game %s "
                            "(game started, scores: %d-%d)",
                            event_id,
                            home_score,
                            away_score,
                        )
                    else:
                        # Build structured team stats from raw dicts
                        home_stats = (
                            NFLTeamGameStats.from_espn_api(home_team_dict)
                            if home_team_dict
                            else NFLTeamGameStats()
                        )
                        away_stats = (
                            NFLTeamGameStats.from_espn_api(away_team_dict)
                            if away_team_dict
                            else NFLTeamGameStats()
                        )
                        events.append(
                            NFLGameUpdateEvent(
                                timestamp=timestamp,
                                game_timestamp=self._compute_game_timestamp(
                                    quarter, game_clock
                                ),
                                game_id=event_id,
                                sport="nfl",
                                period=quarter,
                                game_clock=game_clock,
                                home_score=home_team_dict.get("score", 0)
                                if home_team_dict
                                else 0,
                                away_score=away_team_dict.get("score", 0)
                                if away_team_dict
                                else 0,
                                possession=possession_team,
                                down=down,
                                distance=distance,
                                yard_line=yard_line,
                                home_team_stats=home_stats,
                                away_team_stats=away_stats,
                                home_line_scores=home_line_scores,
                                away_line_scores=away_line_scores,
                            )
                        )
                        # Track emitted scores for score-change detection in PBP
                        self._state.mark_scores_emitted(
                            event_id, home_score, away_score
                        )
                        # Mark final update as emitted if game is concluded
                        if game_concluded:
                            self._state.mark_final_update_emitted(event_id)

        # Parse drives
        drives = data.get("drives", {}) or {}
        previous_drives = drives.get("previous", []) or []
        if previous_drives:
            # Build index mapping: drive_id -> 1-based position in the full list
            drive_index: dict[str, int] = {}
            for idx, d in enumerate(previous_drives):
                if d and isinstance(d, dict):
                    did = str(d.get("id", ""))
                    if did:
                        drive_index[did] = idx + 1

            new_drives = self._state.filter_new_drives(event_id, previous_drives)
            for drive in new_drives:
                if not drive or not isinstance(drive, dict):
                    continue
                d_id = str(drive.get("id", ""))
                d_num = drive_index.get(d_id, 0)
                drive_events = self._parse_drive(
                    event_id, drive, timestamp, drive_number=d_num
                )
                events.extend(drive_events)

        return events

    def _parse_drive(
        self,
        event_id: str,
        drive: dict[str, Any],
        timestamp: datetime,
        drive_number: int = 0,
    ) -> list[DataEvent]:
        """Parse a single drive into events."""
        events: list[DataEvent] = []

        drive_id = str(drive.get("id", ""))
        if not drive_id:
            return events

        # Get team info
        team = drive.get("team", {}) or {}
        team_id = str(team.get("id", ""))
        team_abbreviation = team.get("abbreviation", "")

        # Get drive start/end info
        start = drive.get("start", {}) or {}
        end = drive.get("end", {}) or {}

        events.append(
            NFLDriveEvent(
                timestamp=timestamp,
                game_id=event_id,
                sport="nfl",
                segment_id=drive_id,
                segment_number=drive_number,
                drive_id=drive_id,
                drive_number=drive_number,
                team_id=team_id,
                team_tricode=team_abbreviation,
                start_period=int((start.get("period", {}) or {}).get("number", 0) or 0),
                start_clock=(start.get("clock", {}) or {}).get("displayValue", ""),
                start_yard_line=int(start.get("yardLine", 0) or 0),
                end_period=int((end.get("period", {}) or {}).get("number", 0) or 0),
                end_clock=(end.get("clock", {}) or {}).get("displayValue", ""),
                end_yard_line=int(end.get("yardLine", 0) or 0),
                plays_count=int(drive.get("offensivePlays", 0) or 0),
                yards=int(drive.get("yards", 0) or 0),
                time_elapsed=(drive.get("timeElapsed", {}) or {}).get(
                    "displayValue", ""
                ),
                result=drive.get("result", ""),
                is_score=bool(drive.get("isScore", False)),
                points_scored=self._result_to_points(drive.get("result", "")),
            )
        )

        return events

    def _parse_plays(
        self, data: dict[str, Any], timestamp: datetime
    ) -> list[DataEvent]:
        """Parse play-by-play data into events.

        Emits:
        - NFLPlayEvent for each new play
        - NFLGameUpdateEvent immediately after scoring plays (score-change detection)
        - GameStartEvent when first play is detected
        """
        events: list[DataEvent] = []

        event_id = str(data.get("eventId", ""))
        if not event_id:
            return events

        items = data.get("items", []) or []
        if not items:
            return events

        # Filter to new plays (filter out None items first)
        valid_items = [item for item in items if item and isinstance(item, dict)]
        new_plays = self._state.filter_new_plays(event_id, valid_items)

        # Detect game start
        if new_plays and not self._state.has_game_started(event_id):
            # Include starters if available (fetched during scoreboard prefetch)
            home_tid, away_tid = "", ""
            poll_id = self._poll_identifier or {}
            # Try to extract team IDs from the first play's context
            if poll_id.get("espn_game_id") == event_id:
                for tid in self._roster_cache:
                    if not home_tid:
                        home_tid = tid
                    elif not away_tid:
                        away_tid = tid
            events.append(
                GameStartEvent(
                    timestamp=timestamp,
                    game_timestamp=self._game_start_time,
                    game_id=event_id,
                    sport="nfl",
                    home_starters=self._state.get_starters(event_id, home_tid),
                    away_starters=self._state.get_starters(event_id, away_tid),
                )
            )
            self._state.mark_game_started(event_id)

        # Emit play events
        for play in new_plays:
            play_id = str(play.get("id", ""))
            play_type_info = play.get("type", {})
            play_type = (
                play_type_info.get("text", "")
                if isinstance(play_type_info, dict)
                else ""
            )

            # Get period and clock
            period = play.get("period", {})
            quarter = (
                int(period.get("number", 0) or 0) if isinstance(period, dict) else 0
            )
            clock = play.get("clock", {})
            game_clock = (
                clock.get("displayValue", "") if isinstance(clock, dict) else ""
            )

            # Parse wallclock time (actual UTC time the play occurred)
            play_game_timestamp: datetime | None = None
            wallclock = play.get("wallclock", "")
            if wallclock:
                try:
                    play_game_timestamp = datetime.fromisoformat(
                        str(wallclock).replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass
            # Fall back to computed timestamp from game clock
            if not play_game_timestamp:
                play_game_timestamp = self._compute_game_timestamp(quarter, game_clock)

            # Get scoring info
            is_scoring = bool(play.get("scoringPlay", False))
            score_value = int(play.get("scoreValue", 0) or 0)

            # Get team info — handle both inline dict and $ref URL formats
            team = play.get("team", {})
            team_id = ""
            team_abbrev = ""
            if isinstance(team, dict):
                team_id = str(team.get("id", ""))
                team_abbrev = team.get("abbreviation", "")
                # Core API returns $ref URL like ".../teams/26"
                if not team_id and "$ref" in team:
                    ref = team["$ref"]
                    parts = ref.rstrip("/").split("/")
                    if parts:
                        team_id = parts[-1].split("?")[0]
            # Resolve abbreviation from state tracker if we have team_id but no abbrev
            if team_id and not team_abbrev:
                from dojozero.data.nfl._utils import get_team_abbreviation

                team_abbrev = get_team_abbreviation(team_id) or ""

            # Get start info for down/distance
            start = play.get("start", {})
            down = int(start.get("down", 0) or 0) if isinstance(start, dict) else 0
            distance = (
                int(start.get("distance", 0) or 0) if isinstance(start, dict) else 0
            )
            yard_line = (
                int(start.get("yardLine", 0) or 0) if isinstance(start, dict) else 0
            )

            home_score = int(play.get("homeScore", 0) or 0)
            away_score = int(play.get("awayScore", 0) or 0)

            events.append(
                NFLPlayEvent(
                    timestamp=timestamp,
                    game_timestamp=play_game_timestamp,
                    game_id=event_id,
                    sport="nfl",
                    play_id=play_id,
                    sequence_number=int(play.get("sequenceNumber", 0) or 0),
                    period=quarter,
                    clock=game_clock,
                    down=down,
                    distance=distance,
                    yard_line=yard_line,
                    play_type=play_type,
                    description=play.get("text", ""),
                    yards_gained=int(play.get("statYardage", 0) or 0),
                    is_scoring_play=is_scoring,
                    score_value=score_value,
                    home_score=home_score,
                    away_score=away_score,
                    team_id=team_id,
                    team_tricode=team_abbrev,
                    team_abbreviation=team_abbrev,
                    is_turnover=bool(play.get("isTurnover", False)),
                )
            )

            # Emit immediate game update after scoring plays for real-time score tracking
            if is_scoring and self._state.score_changed(
                event_id, home_score, away_score
            ):
                logger.info(
                    "Score change detected for game %s: %d-%d (scoring play)",
                    event_id,
                    home_score,
                    away_score,
                )
                events.append(
                    NFLGameUpdateEvent(
                        timestamp=timestamp,
                        game_timestamp=play_game_timestamp,
                        game_id=event_id,
                        sport="nfl",
                        period=quarter,
                        game_clock=game_clock,
                        home_score=home_score,
                        away_score=away_score,
                        possession=team_abbrev,
                        down=down,
                        distance=distance,
                        yard_line=yard_line,
                        home_team_stats=NFLTeamGameStats(),
                        away_team_stats=NFLTeamGameStats(),
                        home_line_scores=[],
                        away_line_scores=[],
                    )
                )
                self._state.mark_scores_emitted(event_id, home_score, away_score)

        return events

    def _status_name_to_code(self, status_name: str) -> int:
        """Convert ESPN status name to status code.

        Args:
            status_name: ESPN status name (e.g., "STATUS_SCHEDULED")

        Returns:
            Status code (1=scheduled, 2=in_progress, 3=final)
        """
        status_mapping = {
            "STATUS_SCHEDULED": NFLGameStateTracker.STATUS_SCHEDULED,
            "STATUS_IN_PROGRESS": NFLGameStateTracker.STATUS_IN_PROGRESS,
            "STATUS_HALFTIME": NFLGameStateTracker.STATUS_IN_PROGRESS,
            "STATUS_END_PERIOD": NFLGameStateTracker.STATUS_IN_PROGRESS,
            "STATUS_FINAL": NFLGameStateTracker.STATUS_FINAL,
            "STATUS_FINAL_OVERTIME": NFLGameStateTracker.STATUS_FINAL,
        }
        return status_mapping.get(status_name, NFLGameStateTracker.STATUS_SCHEDULED)

    def _result_to_points(self, result: str) -> int:
        """Convert drive result to points scored.

        Args:
            result: Drive result string (e.g., "Touchdown", "Field Goal")

        Returns:
            Points scored (0, 3, 6, 7, 8, or 2 for safety)
        """
        result_upper = result.strip().upper()
        if result_upper in ("TD", "TOUCHDOWN"):
            return 7  # Assume extra point made
        elif result_upper in ("FG", "FIELD GOAL"):
            return 3
        elif result_upper in ("SF", "SAFETY"):
            return 2
        return 0

    async def _prefetch_rosters_from_scoreboard(
        self, scoreboard_data: dict[str, Any], target_event_id: str
    ) -> None:
        """Extract team IDs from scoreboard and pre-fetch their rosters."""
        sb = scoreboard_data.get("scoreboard", {})
        for game in sb.get("events", []):
            if not game or str(game.get("id", "")) != target_event_id:
                continue
            comps = game.get("competitions", [])
            if not comps:
                continue
            for comp in comps[0].get("competitors", []):
                if not comp or not isinstance(comp, dict):
                    continue
                team = comp.get("team", {}) or {}
                team_id = str(team.get("id", ""))
                if team_id:
                    await self._fetch_roster(team_id)

    async def _fetch_roster(self, team_id: str) -> list[PlayerIdentity]:
        """Fetch and cache team roster from ESPN API.

        Returns cached roster if already fetched.
        """
        if team_id in self._roster_cache:
            return self._roster_cache[team_id]

        players: list[PlayerIdentity] = []
        if not self._api:
            return players

        try:
            result = await self._api.fetch("team_roster", {"team_id": team_id})
            athletes = result.get("team_roster", {}).get("athletes", [])
            # NFL roster API returns position groups: [{position, items: [...]}]
            # NBA roster API returns flat list: [{id, displayName, ...}]
            # Handle both formats, tagging NFL players with their group.
            flat_athletes: list[tuple[dict, str]] = []  # (athlete_dict, group)
            for entry in athletes:
                if not isinstance(entry, dict):
                    continue
                if "displayName" in entry or "fullName" in entry:
                    # Flat format (NBA-style) — no group
                    flat_athletes.append((entry, ""))
                else:
                    # Grouped format (NFL-style) — tag with group name
                    group_name = entry.get("position", "")  # "offense", "defense", etc.
                    for item in entry.get("items", []):
                        flat_athletes.append((item, group_name))

            for a, group in flat_athletes:
                if not isinstance(a, dict):
                    continue
                pid = str(a.get("id", ""))
                pos = a.get("position", {})
                players.append(
                    PlayerIdentity(
                        player_id=pid,
                        name=a.get("displayName", ""),
                        position=pos.get("abbreviation", "")
                        if isinstance(pos, dict)
                        else "",
                        jersey=str(a.get("jersey", "")),
                        headshot_url=f"https://a.espncdn.com/i/headshots/nfl/players/full/{pid}.png"
                        if pid
                        else "",
                        group=group,
                    )
                )
            logger.debug("Fetched %d players for team %s", len(players), team_id)
        except Exception:
            logger.debug("Failed to fetch roster for team %s", team_id, exc_info=True)

        self._roster_cache[team_id] = players
        return players

    async def _fetch_game_starters(
        self, event_id: str, home_team_id: str, away_team_id: str
    ) -> None:
        """Fetch game-day starters from core API and store in state tracker.

        Cross-references game roster entries (which have starter flags but only
        last names) with the team roster cache (which has full details).
        """
        if not self._api:
            return

        for team_id in (home_team_id, away_team_id):
            # Skip if already fetched
            if self._state.get_starters(event_id, team_id):
                continue

            try:
                result = await self._api.fetch(
                    "game_roster",
                    {"event_id": event_id, "team_id": team_id},
                )
                entries = result.get("game_roster", {}).get("entries", [])

                # Build lookup from roster cache: player_id -> PlayerIdentity
                roster_lookup: dict[str, PlayerIdentity] = {}
                for p in self._roster_cache.get(team_id, []):
                    roster_lookup[p.player_id] = p

                starters: list[PlayerIdentity] = []
                for entry in entries:
                    if not isinstance(entry, dict) or not entry.get("starter"):
                        continue
                    # Extract athlete ID from $ref URL
                    ath_ref = (entry.get("athlete") or {}).get("$ref", "")
                    ath_id = ""
                    if "/athletes/" in ath_ref:
                        ath_id = ath_ref.split("/athletes/")[-1].split("?")[0]

                    if ath_id and ath_id in roster_lookup:
                        starters.append(roster_lookup[ath_id])
                    elif ath_id:
                        # Fallback: use game roster entry data (last name only)
                        starters.append(
                            PlayerIdentity(
                                player_id=ath_id,
                                name=entry.get("displayName", ""),
                                position="",
                                jersey=str(entry.get("jersey", "")),
                                headshot_url=f"https://a.espncdn.com/i/headshots/nfl/players/full/{ath_id}.png",
                            )
                        )

                if starters:
                    self._state.set_starters(event_id, team_id, starters)
                    logger.debug(
                        "Fetched %d starters for team %s in game %s",
                        len(starters),
                        team_id,
                        event_id,
                    )
            except Exception:
                logger.debug(
                    "Failed to fetch game starters for team %s in game %s",
                    team_id,
                    event_id,
                    exc_info=True,
                )

    async def _poll_api(
        self,
        event_type: str | None = None,
        identifier: dict[str, Any] | None = None,
    ) -> Sequence[DataEvent]:
        """Poll the ESPN API for NFL updates."""
        if not self._api:
            return []

        events: list[DataEvent] = []
        identifier = identifier or {}

        # Get espn_game_id for filtering (if monitoring a single game)
        espn_game_id = identifier.get("espn_game_id")

        # Poll scoreboard for game status/odds updates
        if self._should_poll_endpoint("scoreboard"):
            scoreboard_params: dict[str, Any] = {}
            if "dates" in identifier:
                scoreboard_params["dates"] = identifier["dates"]
            if "week" in identifier:
                scoreboard_params["week"] = identifier["week"]
            if "seasontype" in identifier:
                scoreboard_params["seasontype"] = identifier["seasontype"]

            scoreboard_data = await self._api.fetch("scoreboard", scoreboard_params)
            if scoreboard_data:
                # Pre-fetch rosters before parsing if game not initialized
                if espn_game_id and not self._state.is_game_initialized(espn_game_id):
                    await self._prefetch_rosters_from_scoreboard(
                        scoreboard_data, espn_game_id
                    )
                    # Also fetch game-day starters once rosters are cached
                    sb = scoreboard_data.get("scoreboard", {})
                    for game in sb.get("events", []):
                        if str(game.get("id", "")) != espn_game_id:
                            continue
                        comps = game.get("competitions", [])
                        if not comps:
                            continue
                        team_ids = []
                        for comp in comps[0].get("competitors", []):
                            tid = str((comp.get("team") or {}).get("id", ""))
                            if tid:
                                team_ids.append(tid)
                        if len(team_ids) >= 2:
                            await self._fetch_game_starters(
                                espn_game_id, team_ids[0], team_ids[1]
                            )

                # Pass espn_game_id to filter to single game (if configured)
                scoreboard_events = self._parse_api_response(
                    scoreboard_data, espn_game_id
                )
                events.extend(scoreboard_events)
                self._record_poll_time("scoreboard")

        # Poll summary for specific game
        if "espn_game_id" in identifier:
            espn_game_id = identifier["espn_game_id"]

            if self._should_poll_endpoint("summary"):
                summary_data = await self._api.fetch(
                    "summary", {"event_id": espn_game_id}
                )
                if summary_data:
                    summary_events = self._parse_api_response(summary_data)
                    events.extend(summary_events)
                    self._record_poll_time("summary")

            if self._should_poll_endpoint("plays"):
                plays_data = await self._api.fetch("plays", {"event_id": espn_game_id})
                if plays_data:
                    plays_events = self._parse_api_response(plays_data)
                    events.extend(plays_events)
                    self._record_poll_time("plays")

        return events
