"""ESPN data store base implementation."""

from datetime import datetime, timezone
from typing import Any, Sequence

from dojozero.data._models import DataEvent
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data.espn._api import ESPNExternalAPI
from dojozero.data.espn._events import (
    ESPNGameEndEvent,
    ESPNGameInitializeEvent,
    ESPNGameStartEvent,
    ESPNGameUpdateEvent,
    ESPNOddsUpdateEvent,
    ESPNPlayEvent,
)
from dojozero.data.espn._state_tracker import ESPNStateTracker


class ESPNStore(DataStore):
    """Base ESPN data store for polling ESPN API and emitting events.

    This is a generic store that works with any ESPN-supported sport/league.
    Sport-specific stores can extend this to add specialized parsing.

    Example:
        # Generic usage
        store = ESPNStore(sport="basketball", league="nba")

        # With custom poll intervals
        store = ESPNStore(
            sport="football",
            league="nfl",
            poll_intervals={"scoreboard": 60.0, "plays": 10.0}
        )
    """

    def __init__(
        self,
        sport: str,
        league: str,
        store_id: str | None = None,
        api: ExternalAPI | None = None,
        poll_intervals: dict[str, float] | None = None,
        event_emitter=None,
    ):
        """Initialize ESPN store.

        Args:
            sport: Sport type (e.g., "football", "basketball", "soccer")
            league: League identifier (e.g., "nfl", "nba", "eng.1")
            store_id: Optional store ID (defaults to "{sport}_{league}_store")
            api: Optional custom API instance
            poll_intervals: Polling intervals per endpoint
            event_emitter: Optional event emitter callback

        Default polling intervals:
        - scoreboard: 60.0 seconds
        - summary: 30.0 seconds
        - plays: 15.0 seconds
        """
        self.sport = sport
        self.league = league

        if store_id is None:
            store_id = f"{sport}_{league}_store"

        if poll_intervals is None:
            poll_intervals = {
                "scoreboard": 60.0,
                "summary": 30.0,
                "plays": 15.0,
            }

        super().__init__(
            store_id,
            api or ESPNExternalAPI(sport=sport, league=league),
            poll_intervals,
            event_emitter,
        )
        self._state = ESPNStateTracker()

    def _parse_api_response(self, data: dict[str, Any]) -> Sequence[DataEvent]:
        """Parse ESPN API response into DataEvents."""
        events: list[DataEvent] = []
        timestamp = datetime.now(timezone.utc)

        if "scoreboard" in data:
            scoreboard_events = self._parse_scoreboard(data["scoreboard"], timestamp)
            events.extend(scoreboard_events)

        if "summary" in data:
            summary_events = self._parse_summary(data["summary"], timestamp)
            events.extend(summary_events)

        if "plays" in data:
            plays_events = self._parse_plays(data["plays"], timestamp)
            events.extend(plays_events)

        return events

    def _parse_scoreboard(
        self, data: dict[str, Any], timestamp: datetime
    ) -> list[DataEvent]:
        """Parse scoreboard data into events.

        Emits:
        - ESPNGameInitializeEvent for new games
        - ESPNOddsUpdateEvent when odds change
        - ESPNGameStartEvent when game starts
        - ESPNGameEndEvent when game ends
        """
        events: list[DataEvent] = []

        scoreboard_events = data.get("events", [])
        for game in scoreboard_events:
            if not isinstance(game, dict):
                continue

            event_id = str(game.get("id", ""))
            if not event_id:
                continue

            competitions = game.get("competitions", [])
            if not competitions:
                continue
            competition = competitions[0]

            competitors = competition.get("competitors", [])
            if len(competitors) < 2:
                continue

            # Identify home and away
            home_data = away_data = None
            for comp in competitors:
                if comp.get("homeAway") == "home":
                    home_data = comp
                elif comp.get("homeAway") == "away":
                    away_data = comp

            if not home_data or not away_data:
                continue

            home_team = home_data.get("team", {})
            away_team = away_data.get("team", {})

            # Emit GameInitializeEvent for new games
            if not self._state.is_game_initialized(event_id):
                game_time_str = game.get("date", "")
                game_time = timestamp
                if game_time_str:
                    try:
                        game_time = datetime.fromisoformat(
                            game_time_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                venue = competition.get("venue", {}).get("fullName", "")

                # Build metadata with sport-specific info
                metadata: dict[str, Any] = {}
                if "week" in game:
                    week_data = game.get("week", {})
                    if isinstance(week_data, dict):
                        metadata["week"] = week_data.get("number", 0)
                season = game.get("season", {})
                if isinstance(season, dict):
                    metadata["season_type"] = season.get("type", 2)
                    metadata["season_year"] = season.get("year", 0)

                events.append(
                    ESPNGameInitializeEvent(
                        timestamp=timestamp,
                        event_id=event_id,
                        sport=self.sport,
                        league=self.league,
                        home_team=home_team.get("displayName", ""),
                        away_team=away_team.get("displayName", ""),
                        home_team_id=str(home_team.get("id", "")),
                        away_team_id=str(away_team.get("id", "")),
                        home_team_abbreviation=home_team.get("abbreviation", ""),
                        away_team_abbreviation=away_team.get("abbreviation", ""),
                        venue=venue,
                        game_time=game_time,
                        metadata=metadata,
                    )
                )
                self._state.mark_game_initialized(event_id)

            # Handle odds updates
            odds_list = competition.get("odds", [])
            if odds_list:
                odds = odds_list[0]
                if self._state.odds_changed(event_id, odds):
                    provider = odds.get("provider", {}).get("name", "")
                    spread = float(odds.get("spread", 0) or 0)
                    home_odds = odds.get("homeTeamOdds", {})
                    away_odds = odds.get("awayTeamOdds", {})

                    events.append(
                        ESPNOddsUpdateEvent(
                            timestamp=timestamp,
                            event_id=event_id,
                            sport=self.sport,
                            league=self.league,
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
                            home_team=home_team.get("displayName", ""),
                            away_team=away_team.get("displayName", ""),
                        )
                    )
                    self._state.set_last_odds(event_id, odds)

            # Handle game status transitions
            status = competition.get("status", {}).get("type", {})
            status_name = status.get("name", "")
            status_code = self._state.status_name_to_code(status_name)
            previous_status = self._state.get_previous_status(event_id)

            if status_code != previous_status:
                # Game started
                if (
                    status_code == ESPNStateTracker.STATUS_IN_PROGRESS
                    and previous_status != ESPNStateTracker.STATUS_IN_PROGRESS
                ):
                    events.append(
                        ESPNGameStartEvent(
                            timestamp=timestamp,
                            event_id=event_id,
                            sport=self.sport,
                            league=self.league,
                        )
                    )

                # Game ended
                if status_code == ESPNStateTracker.STATUS_FINAL:
                    home_score = int(home_data.get("score", 0) or 0)
                    away_score = int(away_data.get("score", 0) or 0)
                    winner = (
                        "home"
                        if home_score > away_score
                        else "away"
                        if away_score > home_score
                        else ""
                    )

                    events.append(
                        ESPNGameEndEvent(
                            timestamp=timestamp,
                            event_id=event_id,
                            sport=self.sport,
                            league=self.league,
                            winner=winner,
                            home_score=home_score,
                            away_score=away_score,
                            home_team=home_team.get("displayName", ""),
                            away_team=away_team.get("displayName", ""),
                        )
                    )

                self._state.set_previous_status(event_id, status_code)

        return events

    def _parse_summary(
        self, data: dict[str, Any], timestamp: datetime
    ) -> list[DataEvent]:
        """Parse game summary data into events.

        Emits ESPNGameUpdateEvent with boxscore data.
        """
        events: list[DataEvent] = []

        event_id = str(data.get("eventId", ""))
        if not event_id:
            return events

        boxscore = data.get("boxscore", {})
        header = data.get("header", {})

        if not boxscore and not header:
            return events

        # Get competition info
        competitions = header.get("competitions", [])
        competition = competitions[0] if competitions else {}
        status = competition.get("status", {})
        competitors = competition.get("competitors", [])

        # Build team data
        home_team_data: dict[str, Any] = {}
        away_team_data: dict[str, Any] = {}
        home_score = away_score = 0

        for comp in competitors:
            team = comp.get("team", {})
            score = int(comp.get("score", 0) or 0)
            line_scores = [
                int(ls.get("value", 0) or 0)
                for ls in comp.get("linescores", [])
                if isinstance(ls, dict)
            ]

            team_data = {
                "team": team,
                "score": score,
                "lineScores": line_scores,
                "records": comp.get("records", []),
            }

            if comp.get("homeAway") == "home":
                home_team_data = team_data
                home_score = score
            else:
                away_team_data = team_data
                away_score = score

        # Add boxscore team stats if available
        for team_stats in boxscore.get("teams", []):
            home_away = team_stats.get("homeAway", "")
            if home_away == "home":
                home_team_data["statistics"] = team_stats.get("statistics", [])
            else:
                away_team_data["statistics"] = team_stats.get("statistics", [])

        events.append(
            ESPNGameUpdateEvent(
                timestamp=timestamp,
                event_id=event_id,
                sport=self.sport,
                league=self.league,
                home_score=home_score,
                away_score=away_score,
                period=int(status.get("period", 0) or 0),
                clock=status.get("displayClock", ""),
                status=status.get("type", {}).get("description", ""),
                home_team_data=home_team_data,
                away_team_data=away_team_data,
                metadata={},
            )
        )

        return events

    def _parse_plays(
        self, data: dict[str, Any], timestamp: datetime
    ) -> list[DataEvent]:
        """Parse play-by-play data into events.

        Emits ESPNPlayEvent for each new play.
        """
        events: list[DataEvent] = []

        event_id = str(data.get("eventId", ""))
        if not event_id:
            return events

        items = data.get("items", [])
        if not items:
            return events

        # Filter to new plays
        new_plays = self._state.filter_new_plays(event_id, items)

        # Detect game start from first play
        if new_plays and not self._state.has_game_started(event_id):
            events.append(
                ESPNGameStartEvent(
                    timestamp=timestamp,
                    event_id=event_id,
                    sport=self.sport,
                    league=self.league,
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

            period = play.get("period", {})
            period_num = (
                int(period.get("number", 0) or 0) if isinstance(period, dict) else 0
            )
            clock = play.get("clock", {})
            clock_display = (
                clock.get("displayValue", "") if isinstance(clock, dict) else ""
            )

            is_scoring = bool(play.get("scoringPlay", False))
            score_value = int(play.get("scoreValue", 0) or 0)

            team = play.get("team", {})
            team_id = str(team.get("id", "")) if isinstance(team, dict) else ""
            team_abbrev = team.get("abbreviation", "") if isinstance(team, dict) else ""

            events.append(
                ESPNPlayEvent(
                    timestamp=timestamp,
                    event_id=event_id,
                    play_id=play_id,
                    sport=self.sport,
                    league=self.league,
                    sequence_number=int(play.get("sequenceNumber", 0) or 0),
                    period=period_num,
                    clock=clock_display,
                    play_type=play_type,
                    description=play.get("text", ""),
                    home_score=int(play.get("homeScore", 0) or 0),
                    away_score=int(play.get("awayScore", 0) or 0),
                    is_scoring_play=is_scoring,
                    score_value=score_value,
                    team_id=team_id,
                    team_abbreviation=team_abbrev,
                    metadata={},
                )
            )

        return events

    async def _poll_api(
        self,
        event_type: str | None = None,
        identifier: dict[str, Any] | None = None,
    ) -> Sequence[DataEvent]:
        """Poll the ESPN API for updates."""
        if not self._api:
            return []

        events: list[DataEvent] = []
        identifier = identifier or {}

        # Poll scoreboard
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
                scoreboard_events = self._parse_api_response(scoreboard_data)
                events.extend(scoreboard_events)
                self._record_poll_time("scoreboard")

        # Poll summary and plays for specific game
        if "event_id" in identifier:
            event_id = identifier["event_id"]

            if self._should_poll_endpoint("summary"):
                summary_data = await self._api.fetch("summary", {"event_id": event_id})
                if summary_data:
                    summary_events = self._parse_api_response(summary_data)
                    events.extend(summary_events)
                    self._record_poll_time("summary")

            if self._should_poll_endpoint("plays"):
                plays_data = await self._api.fetch("plays", {"event_id": event_id})
                if plays_data:
                    plays_events = self._parse_api_response(plays_data)
                    events.extend(plays_events)
                    self._record_poll_time("plays")

        return events
