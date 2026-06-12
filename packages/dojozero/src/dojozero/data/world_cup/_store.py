"""World Cup (FIFA soccer) data store implementation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Sequence

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
from dojozero.data.world_cup._api import DEFAULT_LEAGUE, WorldCupExternalAPI
from dojozero.data.world_cup._events import (
    SoccerGamePlayerStats,
    SoccerPlayerMatchStats,
    SoccerTeamMatchStats,
    WorldCupGameUpdateEvent,
    WorldCupPlayEvent,
)
from dojozero.data.world_cup._state_tracker import GameStateTracker
from dojozero.data.world_cup._utils import (
    _build_game_info_from_summary,
    _id_from_ref,
    parse_iso_datetime,
)


logger = logging.getLogger(__name__)


# ESPN play ``type.type`` slugs that terminate a soccer match. We match on the
# kebab-case slug (e.g. ``end-regular-time``) rather than display text so
# locale changes can't break detection. ``end-regular-time`` covers matches
# that finish in 90 minutes; the rest cover ET, shootouts, and abandonment.
_GAME_END_TYPE_SLUGS = {
    "end-regular-time",
    "end-extra-time",
    "end-shootout",
    "end-match",
    "final-whistle",
    "abandoned",
    "forfeit",
}


# Map of ESPN team-statistic name -> SoccerTeamMatchStats attribute.
_TEAM_STAT_MAP: dict[str, str] = {
    "possessionPct": "possession_pct",
    "totalPasses": "total_passes",
    "accuratePasses": "accurate_passes",
    "passPct": "pass_pct",
    "totalShots": "total_shots",
    "shotsOnTarget": "shots_on_target",
    "blockedShots": "blocked_shots",
    "wonCorners": "corners",
    "offsides": "offsides",
    "totalTackles": "total_tackles",
    "effectiveTackles": "effective_tackles",
    "interceptions": "interceptions",
    "saves": "saves",
    "foulsCommitted": "fouls_committed",
    "yellowCards": "yellow_cards",
    "redCards": "red_cards",
}

# Map of ESPN player-statistic name -> SoccerPlayerMatchStats attribute.
_PLAYER_STAT_MAP: dict[str, str] = {
    "totalGoals": "goals",
    "goalAssists": "assists",
    "totalShots": "total_shots",
    "shotsOnTarget": "shots_on_target",
    "foulsCommitted": "fouls_committed",
    "foulsSuffered": "fouls_suffered",
    "yellowCards": "yellow_cards",
    "redCards": "red_cards",
    "saves": "saves",
    "goalsConceded": "goals_conceded",
}


def _stat_value(stat: dict[str, Any]) -> float:
    """Return numeric value from an ESPN stat entry.

    ESPN ``displayValue`` is a string ("13", "42.6", "0.9"); ``value`` is the
    canonical numeric form when present.
    """
    if "value" in stat and stat["value"] is not None:
        try:
            return float(stat["value"])
        except (TypeError, ValueError):
            return 0.0
    raw = stat.get("displayValue", "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


class WorldCupStore(DataStore):
    """Polls ESPN soccer API and emits world_cup events."""

    sport_type: str = "world_cup"

    _POLL_PROFILES: dict[PollProfile, dict[str, float]] = {
        PollProfile.PRE_GAME: {"boxscore": 120.0, "play_by_play": 60.0},
        PollProfile.IN_GAME: {"boxscore": 30.0, "play_by_play": 10.0},
        PollProfile.LATE_GAME: {"boxscore": 15.0, "play_by_play": 5.0},
    }

    def __init__(
        self,
        store_id: str = "world_cup_store",
        api: ExternalAPI | None = None,
        poll_intervals: dict[str, float] | None = None,
        event_emitter: Any = None,
        league: str = DEFAULT_LEAGUE,
    ):
        if poll_intervals is None:
            poll_intervals = dict(self._POLL_PROFILES[PollProfile.PRE_GAME])
        super().__init__(
            store_id,
            api or WorldCupExternalAPI(league=league),
            poll_intervals,
            event_emitter,
        )
        self.league = league
        self._state = GameStateTracker()
        self._current_poll_profile: PollProfile = PollProfile.PRE_GAME

    # =========================================================================
    # Parsing helpers
    # =========================================================================

    @staticmethod
    def _parse_team_stats_from_boxscore(
        team_box: dict[str, Any], score: int
    ) -> tuple[str, str, str, SoccerTeamMatchStats]:
        """Project an ESPN boxscore team entry into ``SoccerTeamMatchStats``.

        Returns:
            ``(team_id, team_name, team_tricode, stats)`` tuple.
        """
        team = team_box.get("team", {}) or {}
        team_id = str(team.get("id", ""))
        team_name = team.get("displayName", "") or team.get("name", "")
        team_tricode = team.get("abbreviation", "")

        fields: dict[str, Any] = {
            "team_id": team_id,
            "team_name": team_name,
            "team_tricode": team_tricode,
            "score": score,
        }
        for stat in team_box.get("statistics", []) or []:
            if not isinstance(stat, dict):
                continue
            attr = _TEAM_STAT_MAP.get(stat.get("name", ""))
            if not attr:
                continue
            value = _stat_value(stat)
            if attr in {"possession_pct", "pass_pct"}:
                fields[attr] = float(value)
            else:
                fields[attr] = int(value)
        return team_id, team_name, team_tricode, SoccerTeamMatchStats(**fields)

    @staticmethod
    def _parse_player_stats_from_roster(
        roster_side: dict[str, Any],
    ) -> list[SoccerPlayerMatchStats]:
        """Project an ESPN summary ``rosters`` side entry into curated stats."""
        players: list[SoccerPlayerMatchStats] = []
        for entry in roster_side.get("roster", []) or []:
            if not isinstance(entry, dict):
                continue
            athlete = entry.get("athlete", {}) or {}
            position = entry.get("position", {}) or {}
            fields: dict[str, Any] = {
                "player_id": str(athlete.get("id", "")),
                "name": athlete.get("displayName", "") or athlete.get("fullName", ""),
                "jersey": str(entry.get("jersey", "")),
                "position": position.get("abbreviation", "")
                if isinstance(position, dict)
                else "",
                "starter": bool(entry.get("starter", False)),
                "subbed_in": bool(entry.get("subbedIn", False)),
                "subbed_out": bool(entry.get("subbedOut", False)),
            }
            for stat in entry.get("stats", []) or []:
                if not isinstance(stat, dict):
                    continue
                attr = _PLAYER_STAT_MAP.get(stat.get("name", ""))
                if not attr:
                    continue
                fields[attr] = int(_stat_value(stat))
            players.append(SoccerPlayerMatchStats(**fields))
        return players

    @staticmethod
    def _build_player_identities(
        roster_side: dict[str, Any],
    ) -> list[PlayerIdentity]:
        """Build ``PlayerIdentity`` list from a summary ``rosters`` side entry."""
        result: list[PlayerIdentity] = []
        for entry in roster_side.get("roster", []) or []:
            if not isinstance(entry, dict):
                continue
            athlete = entry.get("athlete", {}) or {}
            position = entry.get("position", {}) or {}
            pid = str(athlete.get("id", ""))
            result.append(
                PlayerIdentity(
                    player_id=pid,
                    name=athlete.get("displayName", "") or athlete.get("fullName", ""),
                    position=position.get("abbreviation", "")
                    if isinstance(position, dict)
                    else "",
                    jersey=str(entry.get("jersey", "")),
                    headshot_url=(
                        f"https://a.espncdn.com/i/headshots/soccer/players/full/{pid}.png"
                        if pid
                        else ""
                    ),
                )
            )
        return result

    # =========================================================================
    # Summary endpoint parsing
    # =========================================================================

    def _parse_summary(self, summary: dict[str, Any], events: list[DataEvent]) -> None:
        """Parse an ESPN soccer summary payload, appending events to ``events``."""
        header = summary.get("header", {}) or {}
        competitions = header.get("competitions", []) or []
        if not competitions:
            return
        comp = competitions[0]

        game_id = str(header.get("id", "") or comp.get("id", ""))
        if not game_id:
            return

        timestamp = datetime.now(timezone.utc)

        # Identify competitors by home/away
        competitors = comp.get("competitors", []) or []
        home_competitor: dict[str, Any] = {}
        away_competitor: dict[str, Any] = {}
        for c in competitors:
            if isinstance(c, dict):
                if c.get("homeAway") == "home":
                    home_competitor = c
                elif c.get("homeAway") == "away":
                    away_competitor = c
        has_team_data = bool(home_competitor and away_competitor)

        # Status mapping
        status_name = (comp.get("status", {}) or {}).get("type", {}).get("name", "")
        status_code = self._state.status_name_to_code(status_name)
        prev_status = self._state.get_previous_status(game_id)
        if prev_status is None or status_code != prev_status:
            self._state.set_previous_status(game_id, status_code)

        # Lookup population for PBP enrichment
        if has_team_data:
            home_team = home_competitor.get("team", {}) or {}
            away_team = away_competitor.get("team", {}) or {}
            home_tid = str(home_team.get("id", ""))
            away_tid = str(away_team.get("id", ""))
            if home_tid and away_tid:
                self._state.set_team_ids(game_id, home_tid, away_tid)
            if home_competitor.get("winner") is True:
                self._state.set_winner_side(game_id, "home")
            elif away_competitor.get("winner") is True:
                self._state.set_winner_side(game_id, "away")
            for team in (home_team, away_team):
                tid = str(team.get("id", ""))
                self._state.update_team_lookup(
                    tid,
                    team.get("abbreviation", ""),
                    team.get("displayName", "") or team.get("name", ""),
                )

        # Starting XI from rosters; also populate player name lookup.
        rosters = summary.get("rosters", []) or []
        home_roster: dict[str, Any] = {}
        away_roster: dict[str, Any] = {}
        for r in rosters:
            if not isinstance(r, dict):
                continue
            if r.get("homeAway") == "home":
                home_roster = r
            elif r.get("homeAway") == "away":
                away_roster = r
        for r in (home_roster, away_roster):
            for entry in r.get("roster", []) or []:
                if not isinstance(entry, dict):
                    continue
                athlete = entry.get("athlete", {}) or {}
                self._state.update_player_lookup(
                    str(athlete.get("id", "")),
                    athlete.get("displayName", "") or athlete.get("fullName", ""),
                )
        home_starters = [
            e
            for e in home_roster.get("roster", []) or []
            if isinstance(e, dict) and e.get("starter")
        ]
        away_starters = [
            e
            for e in away_roster.get("roster", []) or []
            if isinstance(e, dict) and e.get("starter")
        ]
        if home_starters or away_starters:
            self._state.set_starters(game_id, home_starters, away_starters)

        # Emit GameInitializeEvent once.
        if has_team_data and not self._state.is_game_initialized(game_id):
            game_info = _build_game_info_from_summary(summary, game_id)
            if game_info is not None:
                home = game_info.home_team
                away = game_info.away_team
                events.append(
                    GameInitializeEvent(
                        timestamp=timestamp,
                        game_id=game_id,
                        sport="world_cup",
                        home_team=TeamIdentity(
                            team_id=home.team_id,
                            name=home.name,
                            tricode=home.tricode,
                            location=home.location,
                            color=home.color,
                            alternate_color=home.alternate_color,
                            logo_url=home.logo,
                            record=home.record,
                            players=self._build_player_identities(home_roster),
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
                            players=self._build_player_identities(away_roster),
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

        # Game update + curated stats.
        if has_team_data:
            home_score = int(home_competitor.get("score", 0) or 0)
            away_score = int(away_competitor.get("score", 0) or 0)
            self._state.update_scores(game_id, home_score, away_score)

            boxscore = summary.get("boxscore", {}) or {}
            box_teams = boxscore.get("teams", []) or []
            home_box: dict[str, Any] = {}
            away_box: dict[str, Any] = {}
            for entry in box_teams:
                if not isinstance(entry, dict):
                    continue
                if entry.get("homeAway") == "home":
                    home_box = entry
                elif entry.get("homeAway") == "away":
                    away_box = entry

            _, _, _, home_stats = self._parse_team_stats_from_boxscore(
                home_box, home_score
            )
            _, _, _, away_stats = self._parse_team_stats_from_boxscore(
                away_box, away_score
            )

            game_time_utc = ""
            raw_date = comp.get("date", "")
            if isinstance(raw_date, str):
                game_time_utc = raw_date

            period = self._state.get_current_period(game_id)
            game_clock = self._state.get_current_clock(game_id)
            if not period and status_code == self._state.STATUS_FINAL:
                # No PBP yet but match is done: infer the terminal phase from
                # ESPN's status name so ET/shootout matches don't look like
                # regulation finishes.
                if status_name == "STATUS_FINAL_AET":
                    period = 4
                    game_clock = "AET"
                elif status_name == "STATUS_FINAL_PEN":
                    period = 5
                    game_clock = "PEN"
                else:
                    period = 2
                    game_clock = "FT"

            events.append(
                WorldCupGameUpdateEvent(
                    timestamp=timestamp,
                    game_id=game_id,
                    sport="world_cup",
                    period=period,
                    game_clock=game_clock,
                    game_time_utc=game_time_utc,
                    home_score=home_score,
                    away_score=away_score,
                    home_team_stats=home_stats,
                    away_team_stats=away_stats,
                    player_stats=SoccerGamePlayerStats(
                        home=self._parse_player_stats_from_roster(home_roster),
                        away=self._parse_player_stats_from_roster(away_roster),
                    ),
                )
            )
            self._state.mark_scores_emitted(game_id, home_score, away_score)

            pbp_available = self._state.is_pbp_available(game_id)
            final_summary_seen = self._state.has_final_summary_seen(game_id)
            should_emit_summary_result = (
                status_code == self._state.STATUS_FINAL
                and not self._state.has_game_result_emitted(game_id)
                and (pbp_available or final_summary_seen)
            )
            if status_code == self._state.STATUS_FINAL:
                self._state.mark_final_summary_seen(game_id)

            # Prefer PBP-owned result emission so play events precede the final
            # result. If final summaries repeat and PBP never appears, emit a
            # summary fallback so the broker still closes.
            if should_emit_summary_result:
                winner = (
                    "home"
                    if home_score > away_score
                    else "away"
                    if away_score > home_score
                    else self._state.get_winner_side(game_id) or "even"
                )
                home_tid = self._state.get_home_team_id(game_id)
                away_tid = self._state.get_away_team_id(game_id)
                events.append(
                    GameResultEvent(
                        timestamp=timestamp,
                        game_id=game_id,
                        sport="world_cup",
                        winner=winner,
                        home_score=home_score,
                        away_score=away_score,
                        home_team_name=self._state.get_team_name(home_tid),
                        away_team_name=self._state.get_team_name(away_tid),
                        home_team_id=home_tid,
                        away_team_id=away_tid,
                    )
                )
                self._state.mark_game_result_emitted(game_id)
                self._state.mark_final_update_emitted(game_id)

    # =========================================================================
    # Plays endpoint parsing
    # =========================================================================

    def _parse_plays(
        self, plays_payload: dict[str, Any], events: list[DataEvent]
    ) -> None:
        """Parse the core-API plays payload, appending events to ``events``."""
        items = plays_payload.get("items", []) or []
        if not items:
            return

        # Resolve game_id from items[0].$ref → ".../events/<id>/competitions/...".
        game_id = plays_payload.get("eventId", "")
        if not game_id and items:
            ref = items[0].get("$ref", "")
            if "/events/" in ref:
                game_id = ref.split("/events/")[1].split("/", 1)[0]
        if not game_id:
            return

        # GameStart on first observation of plays for this match.
        if not self._state.is_pbp_available(game_id):
            self._state.mark_pbp_available(game_id)
            home_tid = self._state.get_home_team_id(game_id)
            away_tid = self._state.get_away_team_id(game_id)
            events.append(
                GameStartEvent(
                    timestamp=datetime.now(timezone.utc),
                    game_id=game_id,
                    sport="world_cup",
                    home_starters=self._build_player_identities(
                        {"roster": self._state.get_home_starters(game_id)}
                    ),
                    away_starters=self._build_player_identities(
                        {"roster": self._state.get_away_starters(game_id)}
                    ),
                )
            )
            if home_tid:  # mark we've at least seen team IDs
                self._state.set_previous_status(game_id, self._state.STATUS_IN_PROGRESS)

        # Detect game end (last play in items) before dedup-filtering so the
        # signal isn't lost if we already saw all plays.
        game_ended = False
        last_play = items[-1] if items else None
        if isinstance(last_play, dict):
            type_slug = str((last_play.get("type", {}) or {}).get("type", ""))
            if type_slug in _GAME_END_TYPE_SLUGS:
                game_ended = True

        new_items = self._state.filter_new_plays(game_id, items)

        timestamp = datetime.now(timezone.utc)
        last_home_score, last_away_score = self._state.get_current_scores(game_id)
        for play in new_items:
            play_id = str(play.get("id", ""))
            type_info = play.get("type", {}) or {}
            action_type = type_info.get("text", "")
            action_type_id = str(type_info.get("id", ""))
            period_info = play.get("period", {}) or {}
            period = int(period_info.get("number", 0) or 0)
            clock_info = play.get("clock", {}) or {}
            clock_display = clock_info.get("displayValue", "") or ""
            home_score = int(play.get("homeScore", 0) or 0)
            away_score = int(play.get("awayScore", 0) or 0)
            description = play.get("text", "") or play.get("alternativeText", "") or ""
            scoring_play = bool(play.get("scoringPlay", False))
            score_value = int(play.get("scoreValue", 0) or 0)

            team_id = _id_from_ref(play.get("team"))
            team_tricode = self._state.get_team_tricode(team_id)

            # Primary participant (scorer/keeper/etc.) is the first listed.
            player_id = ""
            player_name = ""
            participants = play.get("participants", []) or []
            if participants and isinstance(participants[0], dict):
                player_id = _id_from_ref(participants[0].get("athlete"))
                if player_id:
                    player_name = self._state.get_player_name(player_id)

            # Track clock progression.
            if period:
                self._state.update_match_clock(game_id, period, clock_display)
            self._state.update_scores(game_id, home_score, away_score)

            game_timestamp: datetime | None = None
            wallclock = play.get("wallclock")
            if isinstance(wallclock, str) and wallclock:
                try:
                    game_timestamp = parse_iso_datetime(wallclock)
                except ValueError:
                    game_timestamp = None

            events.append(
                WorldCupPlayEvent(
                    timestamp=timestamp,
                    game_timestamp=game_timestamp,
                    game_id=game_id,
                    sport="world_cup",
                    play_id=play_id,
                    period=period,
                    clock=clock_display,
                    description=description,
                    home_score=home_score,
                    away_score=away_score,
                    team_id=team_id,
                    team_tricode=team_tricode,
                    is_scoring_play=scoring_play,
                    score_value=score_value,
                    action_type=action_type,
                    action_type_id=action_type_id,
                    player_id=player_id,
                    player_name=player_name,
                )
            )

            last_home_score = home_score
            last_away_score = away_score

        # Emit GameResultEvent after all play events so the result is the
        # final event in the stream.
        if game_ended and not self._state.has_game_result_emitted(game_id):
            winner = (
                "home"
                if last_home_score > last_away_score
                else "away"
                if last_away_score > last_home_score
                else self._state.get_winner_side(game_id) or "even"
            )
            home_tid = self._state.get_home_team_id(game_id)
            away_tid = self._state.get_away_team_id(game_id)
            events.append(
                GameResultEvent(
                    timestamp=datetime.now(timezone.utc),
                    game_id=game_id,
                    sport="world_cup",
                    winner=winner,
                    home_score=last_home_score,
                    away_score=last_away_score,
                    home_team_name=self._state.get_team_name(home_tid),
                    away_team_name=self._state.get_team_name(away_tid),
                    home_team_id=home_tid,
                    away_team_id=away_tid,
                )
            )
            self._state.mark_game_result_emitted(game_id)
            self._state.set_previous_status(game_id, self._state.STATUS_FINAL)

    # =========================================================================
    # DataStore overrides
    # =========================================================================

    def _parse_api_response(self, data: dict[str, Any]) -> Sequence[DataEvent]:
        """Dispatch by top-level key: ``summary`` or ``plays``."""
        events: list[DataEvent] = []

        if "summary" in data:
            summary = data["summary"]
            if isinstance(summary, dict):
                self._parse_summary(summary, events)

        if "plays" in data:
            plays = data["plays"]
            if isinstance(plays, dict):
                self._parse_plays(plays, events)

        # Refresh poll profile based on current state.
        game_id = self._poll_identifier.get("espn_game_id", "")
        if game_id:
            new_profile = self._state.get_poll_profile(game_id)
            if new_profile != self._current_poll_profile:
                intervals = self._POLL_PROFILES.get(new_profile)
                if intervals:
                    for endpoint, interval in intervals.items():
                        self.update_poll_interval(endpoint, interval)
                self._current_poll_profile = new_profile

        return events

    async def _poll_api(
        self,
        event_type: str | None = None,
        identifier: dict[str, Any] | None = None,
    ) -> Sequence[DataEvent]:
        """Poll summary + plays for the configured game."""
        if not self._api:
            return []

        events: list[DataEvent] = []
        if not (identifier and "espn_game_id" in identifier):
            return events

        espn_game_id = identifier["espn_game_id"]

        if self._should_poll_endpoint("boxscore"):
            summary_data = await self._api.fetch("summary", {"event_id": espn_game_id})
            if summary_data:
                events.extend(self._parse_api_response(summary_data))
                self._record_poll_time("boxscore")

        if self._should_poll_endpoint("play_by_play"):
            plays_data = await self._api.fetch("plays", {"event_id": espn_game_id})
            if plays_data:
                events.extend(self._parse_api_response(plays_data))
                self._record_poll_time("play_by_play")

        return events

    # =========================================================================
    # State persistence
    # =========================================================================

    async def save_state(self) -> dict[str, Any]:
        base_state = await super().save_state()
        base_state.update(
            {
                "state_tracker": self._state.to_dict(),
                "current_poll_profile": self._current_poll_profile.value,
                "league": self.league,
            }
        )
        return base_state

    async def load_state(
        self,
        state: dict[str, Any],
        dedup_keys: set[str] | None = None,
    ) -> None:
        await super().load_state(state, dedup_keys)
        tracker_data = state.get("state_tracker")
        if tracker_data:
            self._state.load_from_dict(tracker_data)

        if dedup_keys is not None:
            play_ids = {k for k in dedup_keys if "_play_" in k}
            if play_ids:
                self._state.rebuild_dedup_from_play_ids(play_ids)

        league = state.get("league")
        if isinstance(league, str):
            self.league = league

        poll_profile_value = state.get("current_poll_profile")
        if poll_profile_value:
            try:
                self._current_poll_profile = PollProfile(poll_profile_value)
                if self._current_poll_profile in self._POLL_PROFILES:
                    self.poll_intervals = dict(
                        self._POLL_PROFILES[self._current_poll_profile]
                    )
            except ValueError:
                pass


__all__ = ["WorldCupStore"]
