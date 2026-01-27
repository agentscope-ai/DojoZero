"""NFL data store implementation."""

from datetime import datetime, timezone
from typing import Any, Sequence

from dojozero.data._models import DataEvent
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data.nfl._api import NFLExternalAPI
from dojozero.data.nfl._events import (
    NFLDriveEvent,
    NFLGameInitializeEvent,
    NFLGameResultEvent,
    NFLGameStartEvent,
    NFLGameUpdateEvent,
    NFLOddsUpdateEvent,
    NFLPlayEvent,
)
from dojozero.data.nfl._state_tracker import NFLGameStateTracker


class NFLStore(DataStore):
    """NFL data store for polling ESPN API and emitting events."""

    sport_type: str = "nfl"

    def __init__(
        self,
        store_id: str = "nfl_store",
        api: ExternalAPI | None = None,
        poll_intervals: dict[str, float] | None = None,
        event_emitter=None,
    ):
        """Initialize NFL store.

        Default polling intervals:
        - scoreboard: 60.0 seconds (check for new games, odds updates)
        - summary: 30.0 seconds (boxscore updates during games)
        - plays: 10.0 seconds (play-by-play during games)
        """
        if poll_intervals is None:
            poll_intervals = {
                "scoreboard": 60.0,
                "summary": 30.0,
                "plays": 10.0,
            }

        super().__init__(
            store_id,
            api or NFLExternalAPI(),
            poll_intervals,
            event_emitter,
        )
        self._state = NFLGameStateTracker()

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
        - NFLGameInitializeEvent for new games
        - NFLOddsUpdateEvent when odds change
        - NFLGameStartEvent when game status changes to in_progress
        - NFLGameResultEvent when game status changes to final
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

                # Get venue
                venue = competition.get("venue", {}).get("fullName", "")

                # Get week and season type
                week = (
                    game.get("week", {}).get("number", 0)
                    if isinstance(game.get("week"), dict)
                    else 0
                )
                season = game.get("season", {})
                season_type = season.get("type", 2) if isinstance(season, dict) else 2

                events.append(
                    NFLGameInitializeEvent(
                        timestamp=timestamp,
                        event_id=event_id,
                        home_team=home_team_info.get("displayName", ""),
                        away_team=away_team_info.get("displayName", ""),
                        home_team_id=str(home_team_info.get("id", "")),
                        away_team_id=str(away_team_info.get("id", "")),
                        home_team_abbreviation=home_team_info.get("abbreviation", ""),
                        away_team_abbreviation=away_team_info.get("abbreviation", ""),
                        venue=venue,
                        game_time=game_time,
                        week=week,
                        season_type=season_type,
                    )
                )
                self._state.mark_game_initialized(event_id)

            # Handle odds updates from ESPN scoreboard
            # ESPN provides sportsbook odds from providers like DraftKings, FanDuel
            odds_list = competition.get("odds", []) or []
            if odds_list and odds_list[0] and isinstance(odds_list[0], dict):
                odds = odds_list[0]  # Use first provider
                if self._state.odds_changed(event_id, odds):
                    # Extract odds data
                    provider_data = odds.get("provider", {}) or {}
                    provider = provider_data.get("name", "")
                    spread = float(odds.get("spread", 0) or 0)

                    # Get team-specific odds
                    home_odds = odds.get("homeTeamOdds", {}) or {}
                    away_odds = odds.get("awayTeamOdds", {}) or {}

                    events.append(
                        NFLOddsUpdateEvent(
                            timestamp=timestamp,
                            event_id=event_id,
                            provider=provider,
                            spread=spread,
                            spread_odds_home=int(
                                home_odds.get("spreadOdds", -110) or -110
                            ),
                            spread_odds_away=int(
                                away_odds.get("spreadOdds", -110) or -110
                            ),
                            over_under=float(odds.get("overUnder", 0) or 0),
                            over_odds=int(odds.get("overOdds", -110) or -110),
                            under_odds=int(odds.get("underOdds", -110) or -110),
                            moneyline_home=int(home_odds.get("moneyLine", 0) or 0),
                            moneyline_away=int(away_odds.get("moneyLine", 0) or 0),
                            home_team=home_team_info.get("displayName", ""),
                            away_team=away_team_info.get("displayName", ""),
                        )
                    )
                    self._state.set_last_odds(event_id, odds)

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
                    events.append(
                        NFLGameStartEvent(
                            timestamp=timestamp,
                            event_id=event_id,
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

                    events.append(
                        NFLGameResultEvent(
                            timestamp=timestamp,
                            event_id=event_id,
                            winner=winner,
                            final_score={"home": home_score, "away": away_score},
                            home_team=home_team_info.get("displayName", ""),
                            away_team=away_team_info.get("displayName", ""),
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
                game_clock = status.get("displayClock", "")

                # Get possession and down info from situation
                situation = data.get("situation", {}) or {}
                possession_team = situation.get("possession", "")
                down = int(situation.get("down", 0) or 0)
                distance = int(situation.get("distance", 0) or 0)
                yard_line = situation.get("yardLine", "")

                # Get line scores from header competitors
                home_line_scores: list[int] = []
                away_line_scores: list[int] = []
                competitors = competition.get("competitors", []) or []
                for comp in competitors:
                    if not comp or not isinstance(comp, dict):
                        continue
                    line_scores = comp.get("linescores", []) or []
                    scores = [
                        int(ls.get("value", 0) or 0)
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

                # Only emit update if we should (skip duplicates for concluded games)
                if should_emit_update:
                    events.append(
                        NFLGameUpdateEvent(
                            timestamp=timestamp,
                            event_id=event_id,
                            quarter=quarter,
                            game_clock=game_clock,
                            possession=possession_team,
                            down=down,
                            distance=distance,
                            yard_line=yard_line,
                            home_team=home_team_dict,
                            away_team=away_team_dict,
                            home_line_scores=home_line_scores,
                            away_line_scores=away_line_scores,
                        )
                    )
                    # Mark final update as emitted if game is concluded
                    if game_concluded:
                        self._state.mark_final_update_emitted(event_id)

        # Parse drives
        drives = data.get("drives", {}) or {}
        previous_drives = drives.get("previous", []) or []
        if previous_drives:
            new_drives = self._state.filter_new_drives(event_id, previous_drives)
            for drive in new_drives:
                if not drive or not isinstance(drive, dict):
                    continue
                drive_events = self._parse_drive(event_id, drive, timestamp)
                events.extend(drive_events)

        return events

    def _parse_drive(
        self, event_id: str, drive: dict[str, Any], timestamp: datetime
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
                event_id=event_id,
                drive_id=drive_id,
                drive_number=len(
                    [
                        d
                        for d in self._state._seen_drive_ids
                        if d.startswith(f"{event_id}_drive_")
                    ]
                ),
                team_id=team_id,
                team_abbreviation=team_abbreviation,
                start_quarter=int(
                    (start.get("period", {}) or {}).get("number", 0) or 0
                ),
                start_clock=(start.get("clock", {}) or {}).get("displayValue", ""),
                start_yard_line=int(start.get("yardLine", 0) or 0),
                end_quarter=int((end.get("period", {}) or {}).get("number", 0) or 0),
                end_clock=(end.get("clock", {}) or {}).get("displayValue", ""),
                end_yard_line=int(end.get("yardLine", 0) or 0),
                plays=int(drive.get("offensivePlays", 0) or 0),
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
        - NFLGameStartEvent when first play is detected
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
            events.append(
                NFLGameStartEvent(
                    timestamp=timestamp,
                    event_id=event_id,
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

            # Get scoring info
            is_scoring = bool(play.get("scoringPlay", False))
            score_value = int(play.get("scoreValue", 0) or 0)

            # Get team info
            team = play.get("team", {})
            team_id = ""
            team_abbrev = ""
            if isinstance(team, dict):
                team_id = str(team.get("id", ""))
                team_abbrev = team.get("abbreviation", "")

            # Get start info for down/distance
            start = play.get("start", {})
            down = int(start.get("down", 0) or 0) if isinstance(start, dict) else 0
            distance = (
                int(start.get("distance", 0) or 0) if isinstance(start, dict) else 0
            )
            yard_line = (
                int(start.get("yardLine", 0) or 0) if isinstance(start, dict) else 0
            )

            events.append(
                NFLPlayEvent(
                    timestamp=timestamp,
                    event_id=event_id,
                    play_id=play_id,
                    sequence_number=int(play.get("sequenceNumber", 0) or 0),
                    quarter=quarter,
                    game_clock=game_clock,
                    down=down,
                    distance=distance,
                    yard_line=yard_line,
                    play_type=play_type,
                    description=play.get("text", ""),
                    yards_gained=int(play.get("statYardage", 0) or 0),
                    is_scoring_play=is_scoring,
                    score_value=score_value,
                    home_score=int(play.get("homeScore", 0) or 0),
                    away_score=int(play.get("awayScore", 0) or 0),
                    team_id=team_id,
                    team_abbreviation=team_abbrev,
                    is_turnover=bool(play.get("isTurnover", False)),
                )
            )

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
        result_lower = result.lower()
        if "touchdown" in result_lower:
            return 7  # Assume extra point made
        elif "field goal" in result_lower:
            return 3
        elif "safety" in result_lower:
            return 2
        return 0

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
