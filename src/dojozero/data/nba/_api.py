"""NBA ExternalAPI implementation using ESPN API."""

import logging
from typing import Any

from dojozero.data._stores import ExternalAPI
from dojozero.data.espn import ESPNExternalAPI

logger = logging.getLogger(__name__)

# ESPN play type IDs
_ESPN_GAME_END_TYPE_ID = "13"


class NBAExternalAPI(ExternalAPI):
    """ESPN NBA API implementation.

    Wraps the generic ESPNExternalAPI with sport="basketball" and league="nba".

    Endpoints:
    - scoreboard: Get all games for a date
    - summary: Get full game data by event_id (replaces boxscore)
    - plays: Get play-by-play data by event_id
    - teams: Get all NBA teams

    Proxy support:
    - Set DOJOZERO_PROXY_URL environment variable to use a proxy
    - Example: export DOJOZERO_PROXY_URL="http://proxy.example.com:8080"
    """

    def __init__(self, timeout: int = 30, proxy: str | None = None):
        """Initialize NBA API.

        Args:
            timeout: Request timeout in seconds
            proxy: Optional proxy URL. If not provided, will use DOJOZERO_PROXY_URL env var
        """
        super().__init__()
        self._api = ESPNExternalAPI(
            sport="basketball",
            league="nba",
            timeout=timeout,
            proxy=proxy,
        )

    async def fetch(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch NBA data from ESPN API.

        Args:
            endpoint: API endpoint. Supports:
                - "scoreboard": Get games for a date (params: dates=YYYYMMDD)
                - "summary": Get game summary (params: event_id)
                - "plays": Get play-by-play (params: event_id)
                - "teams": Get all teams
                - Legacy endpoints (mapped to ESPN equivalents):
                  - "boxscore": Maps to "summary" (params: game_id -> event_id)
                  - "play_by_play": Maps to "plays" (params: game_id -> event_id)
            params: Request parameters (varies by endpoint)

        Returns:
            API response as dict
        """
        params = params or {}

        # Map legacy endpoints to ESPN equivalents
        if endpoint == "boxscore":
            # Legacy: boxscore with game_id -> ESPN: summary with event_id
            event_id = params.get("game_id") or params.get("event_id")
            if not event_id:
                return {"boxscore": {"gameId": ""}}

            result = await self._api.fetch("summary", {"event_id": event_id})
            summary = result.get("summary", {})

            # Convert ESPN summary to boxscore format expected by _store.py
            return self._convert_summary_to_boxscore(summary, event_id)

        elif endpoint == "play_by_play":
            # Legacy: play_by_play with game_id -> ESPN: plays with event_id
            event_id = params.get("game_id") or params.get("event_id")
            if not event_id:
                return {"play_by_play": {"gameId": "", "actions": []}}

            result = await self._api.fetch("plays", {"event_id": event_id})
            plays = result.get("plays", {})

            # Convert ESPN plays to play_by_play format expected by _store.py
            return self._convert_plays_to_play_by_play(plays, event_id)

        elif endpoint == "scoreboard":
            return await self._api.fetch("scoreboard", params)

        elif endpoint == "summary":
            return await self._api.fetch("summary", params)

        elif endpoint == "plays":
            return await self._api.fetch("plays", params)

        elif endpoint == "teams":
            return await self._api.fetch("teams", params)

        else:
            logger.warning("Unknown endpoint: %s", endpoint)
            return {}

    def _convert_summary_to_boxscore(
        self, summary: dict[str, Any] | None, event_id: str
    ) -> dict[str, Any]:
        """Convert ESPN summary response to boxscore format.

        Args:
            summary: ESPN summary response (may be None if API returns null)
            event_id: The event ID

        Returns:
            Boxscore dict in legacy format expected by _store.py
        """
        if not summary or not isinstance(summary, dict) or "boxscore" not in summary:
            return {"boxscore": {"gameId": event_id}}

        espn_boxscore = summary.get("boxscore", {}) or {}
        teams = espn_boxscore.get("teams", []) or []

        # Find home and away teams
        home_team_data: dict[str, Any] = {}
        away_team_data: dict[str, Any] = {}

        for team in teams:
            if not team or not isinstance(team, dict):
                continue
            team_info = team.get("team", {}) or {}
            # ESPN uses homeAway field to identify home/away
            if team.get("homeAway") == "home":
                home_team_data = self._extract_team_data(team, team_info)
            elif team.get("homeAway") == "away":
                away_team_data = self._extract_team_data(team, team_info)

        # Also check header for additional info
        header = summary.get("header", {}) or {}
        competitions = header.get("competitions", []) or []
        if competitions and competitions[0] and isinstance(competitions[0], dict):
            comp = competitions[0]
            for competitor in comp.get("competitors", []) or []:
                if not competitor or not isinstance(competitor, dict):
                    continue
                if competitor.get("homeAway") == "home" and not home_team_data:
                    home_team_data = self._extract_competitor_data(competitor)
                elif competitor.get("homeAway") == "away" and not away_team_data:
                    away_team_data = self._extract_competitor_data(competitor)
                # Update scores from header (more reliable for live games)
                score = competitor.get("score", "0")
                if competitor.get("homeAway") == "home" and home_team_data:
                    home_team_data["statistics"] = home_team_data.get("statistics", {})
                    home_team_data["statistics"]["points"] = int(score) if score else 0
                elif competitor.get("homeAway") == "away" and away_team_data:
                    away_team_data["statistics"] = away_team_data.get("statistics", {})
                    away_team_data["statistics"]["points"] = int(score) if score else 0

        return {
            "boxscore": {
                "gameId": event_id,
                "homeTeam": home_team_data,
                "awayTeam": away_team_data,
            }
        }

    def _extract_team_data(
        self, team: dict[str, Any], team_info: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract team data from ESPN boxscore team entry."""
        # Extract statistics
        stats_list = team.get("statistics", []) or []
        statistics: dict[str, Any] = {}
        for stat in stats_list:
            if not stat or not isinstance(stat, dict):
                continue
            stat_name = stat.get("name", "")
            stat_value = stat.get("displayValue", "0")
            try:
                # Try to parse as number
                if "." in str(stat_value):
                    statistics[stat_name] = float(stat_value)
                else:
                    statistics[stat_name] = int(stat_value)
            except (ValueError, TypeError):
                statistics[stat_name] = stat_value

        # Extract players
        players = []
        for player_entry in team.get("players", []) or []:
            if not player_entry or not isinstance(player_entry, dict):
                continue
            for stat_entry in player_entry.get("statistics", []) or []:
                if not stat_entry or not isinstance(stat_entry, dict):
                    continue
                for athlete in stat_entry.get("athletes", []) or []:
                    if not athlete or not isinstance(athlete, dict):
                        continue
                    player_data = self._extract_player_data(athlete, stat_entry)
                    if player_data:
                        players.append(player_data)

        return {
            "teamId": team_info.get("id", ""),
            "teamName": team_info.get("name", ""),
            "teamCity": team_info.get("location", ""),
            "teamTricode": team_info.get("abbreviation", ""),
            "statistics": statistics,
            "players": players,
        }

    def _extract_competitor_data(self, competitor: dict[str, Any]) -> dict[str, Any]:
        """Extract team data from ESPN header competitor."""
        team_info = competitor.get("team", {}) or {}
        score = competitor.get("score", "0")

        return {
            "teamId": team_info.get("id", ""),
            "teamName": team_info.get("name", ""),
            "teamCity": team_info.get("location", ""),
            "teamTricode": team_info.get("abbreviation", ""),
            "statistics": {"points": int(score) if score else 0},
            "players": [],
        }

    def _extract_player_data(
        self, athlete: dict[str, Any], stat_entry: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Extract player data from ESPN athlete entry."""
        athlete_info = athlete.get("athlete", {})
        if not athlete_info:
            return None

        # Get stat keys and values
        stat_keys = stat_entry.get("keys", [])
        stat_values = athlete.get("stats", [])

        # Build statistics dict
        statistics: dict[str, Any] = {}
        for i, key in enumerate(stat_keys):
            if i < len(stat_values):
                val = stat_values[i]
                try:
                    if "-" in str(val):
                        # Handle "5-10" format (made-attempted)
                        statistics[key] = val
                    elif "." in str(val):
                        statistics[key] = float(val)
                    else:
                        statistics[key] = int(val)
                except (ValueError, TypeError):
                    statistics[key] = val

        return {
            "personId": athlete_info.get("id", 0),
            "name": athlete_info.get("displayName", ""),
            "position": athlete_info.get("position", {}).get("abbreviation", ""),
            "statistics": statistics,
        }

    def _convert_plays_to_play_by_play(
        self, plays: dict[str, Any] | None, event_id: str
    ) -> dict[str, Any]:
        """Convert ESPN plays response to play_by_play format.

        Args:
            plays: ESPN plays response (may be None if API returns null)
            event_id: The event ID

        Returns:
            play_by_play dict in legacy format expected by _store.py
        """
        actions = []
        if not plays or not isinstance(plays, dict):
            return {
                "play_by_play": {
                    "gameId": event_id,
                    "actions": actions,
                }
            }
        items = plays.get("items", []) or []

        for i, item in enumerate(items):
            action = self._convert_play_to_action(item, i)
            if action:
                actions.append(action)

        return {
            "play_by_play": {
                "gameId": event_id,
                "actions": actions,
            }
        }

    def _convert_play_to_action(
        self, play: dict[str, Any], index: int
    ) -> dict[str, Any] | None:
        """Convert a single ESPN play to action format."""
        if not play or not isinstance(play, dict):
            return None

        # Extract play type
        play_type = play.get("type", {}) or {}
        action_type = play_type.get("text", "") if isinstance(play_type, dict) else ""

        # Extract team info
        team = play.get("team", {})
        team_tricode = team.get("abbreviation", "") if team else ""
        team_id = team.get("id", "") if team else ""

        # Extract period and clock
        period = play.get("period", {})
        period_num = period.get("number", 0) if isinstance(period, dict) else 0
        clock = play.get("clock", {})
        clock_str = clock.get("displayValue", "") if isinstance(clock, dict) else ""

        # Extract scores
        home_score = play.get("homeScore", 0) or 0
        away_score = play.get("awayScore", 0) or 0

        # Extract description
        description = play.get("text", "")

        # Extract participant (player)
        participants = play.get("participants", []) or []
        person_id = 0
        player_name = ""
        if participants and participants[0] and isinstance(participants[0], dict):
            athlete = participants[0].get("athlete", {}) or {}
            person_id = athlete.get("id", 0) if athlete else 0
            player_name = athlete.get("displayName", "") if athlete else ""

        # Check for game end
        if play.get("type", {}).get("id") == _ESPN_GAME_END_TYPE_ID:
            action_type = "game"
            description = "Game End"

        # Extract scoring info
        scoring_play = play.get("scoringPlay", False)
        score_value = play.get("scoreValue", 0) or 0

        # Play ID from ESPN (sequenceNumber or id)
        play_id = str(play.get("sequenceNumber", "")) or str(play.get("id", ""))

        return {
            "actionNumber": index,
            "actionType": action_type.lower() if action_type else "",
            "period": period_num,
            "clock": clock_str,
            "personId": person_id,
            "playerName": player_name,
            "teamId": str(team_id),
            "teamTricode": team_tricode,
            "scoreHome": home_score,
            "scoreAway": away_score,
            "description": description,
            "timeActual": play.get("wallclock", ""),
            "scoringPlay": scoring_play,
            "scoreValue": int(score_value) if score_value else 0,
            "playId": play_id,
        }

    async def close(self) -> None:
        """Close the underlying API session."""
        await self._api.close()
