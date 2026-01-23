"""Game Discovery for DojoZero Dashboard Server.

Provides unified interfaces for fetching game information from
NBA API and ESPN API (for NFL).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

LOGGER = logging.getLogger("dojozero.game_discovery")


@dataclass(slots=True)
class TeamInfo:
    """Team information."""

    team_id: str
    name: str
    tricode: str  # e.g., "LAL", "BOS"
    score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "name": self.name,
            "tricode": self.tricode,
            "score": self.score,
        }


@dataclass(slots=True)
class GameInfo:
    """Unified game information across sports."""

    event_id: str  # game_id for NBA, event_id for NFL
    sport_type: str  # "nba" or "nfl"
    status: int  # 1=scheduled, 2=in_progress, 3=finished
    status_text: str
    game_time_utc: datetime | None
    home_team: TeamInfo
    away_team: TeamInfo
    venue: str = ""
    broadcast: str = ""
    short_name: str = ""  # e.g., "KC @ BUF"
    odds: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sport_type": self.sport_type,
            "status": self.status,
            "status_text": self.status_text,
            "game_time_utc": self.game_time_utc.isoformat()
            if self.game_time_utc
            else None,
            "home_team": self.home_team.to_dict(),
            "away_team": self.away_team.to_dict(),
            "venue": self.venue,
            "broadcast": self.broadcast,
            "short_name": self.short_name,
            "odds": self.odds,
        }


class NBAGameFetcher:
    """Fetches NBA game information from NBA API."""

    async def fetch_games_for_date(
        self,
        date: str | None = None,
    ) -> list[GameInfo]:
        """Fetch NBA games for a specific date.

        Args:
            date: Date in YYYY-MM-DD format. If None, uses today.

        Returns:
            List of GameInfo objects.
        """
        import asyncio

        from dojozero.data.nba._utils import get_games_for_date

        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        try:
            # Run in thread pool to avoid blocking the event loop
            games_raw = await asyncio.to_thread(get_games_for_date, date, False)
        except Exception as e:
            LOGGER.error("Error fetching NBA games for %s: %s", date, e)
            return []

        games: list[GameInfo] = []
        for g in games_raw:
            game_time_utc = None
            if g.get("gameTimeUTC"):
                try:
                    from dateutil import parser

                    game_time_utc = parser.parse(g["gameTimeUTC"])
                    if game_time_utc.tzinfo is None:
                        game_time_utc = game_time_utc.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            home_team = TeamInfo(
                team_id=str(g.get("homeTeam", {}).get("teamId", "")),
                name=g.get("homeTeam", {}).get("teamName", ""),
                tricode=g.get("homeTeam", {}).get("teamTricode", ""),
                score=g.get("homeTeam", {}).get("score", 0) or 0,
            )
            away_team = TeamInfo(
                team_id=str(g.get("awayTeam", {}).get("teamId", "")),
                name=g.get("awayTeam", {}).get("teamName", ""),
                tricode=g.get("awayTeam", {}).get("teamTricode", ""),
                score=g.get("awayTeam", {}).get("score", 0) or 0,
            )

            game = GameInfo(
                event_id=str(g.get("gameId", "")),
                sport_type="nba",
                status=g.get("gameStatus", 1),
                status_text=g.get("gameStatusText", ""),
                game_time_utc=game_time_utc,
                home_team=home_team,
                away_team=away_team,
                short_name=f"{away_team.tricode} @ {home_team.tricode}",
            )
            games.append(game)

        return games

    async def fetch_games_for_date_range(
        self,
        start_date: str,
        end_date: str,
    ) -> list[GameInfo]:
        """Fetch NBA games for a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            List of GameInfo objects.
        """
        games: list[GameInfo] = []
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        if start > end:
            start, end = end, start

        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            day_games = await self.fetch_games_for_date(date_str)
            games.extend(day_games)
            current += timedelta(days=1)

        return games

    async def get_game_status(
        self, game_id: str, game_date: str | None = None
    ) -> int | None:
        """Get current status of a game.

        Args:
            game_id: NBA game ID
            game_date: Date of the game (YYYY-MM-DD). If None, searches recent dates.

        Returns:
            Game status (1=scheduled, 2=in_progress, 3=finished) or None if not found.
        """
        import asyncio

        from dojozero.data.nba._utils import get_games_for_date

        if game_date:
            dates_to_check = [game_date]
        else:
            # Check today and yesterday
            today = datetime.now()
            dates_to_check = [
                today.strftime("%Y-%m-%d"),
                (today - timedelta(days=1)).strftime("%Y-%m-%d"),
            ]

        for date in dates_to_check:
            try:
                # Run in thread pool to avoid blocking the event loop
                games = await asyncio.to_thread(get_games_for_date, date, False)
                for g in games:
                    if str(g.get("gameId")) == game_id:
                        return g.get("gameStatus")
            except Exception as e:
                LOGGER.warning(
                    "Error checking game status for %s on %s: %s", game_id, date, e
                )

        return None


class NFLGameFetcher:
    """Fetches NFL game information from ESPN API."""

    async def fetch_games_for_date(
        self,
        date: str | None = None,
    ) -> list[GameInfo]:
        """Fetch NFL games for a specific date.

        Args:
            date: Date in YYYY-MM-DD format. If None, fetches current scoreboard
                  (which includes upcoming games for the current week).

        Returns:
            List of GameInfo objects.
        """
        from dojozero.data.nfl._api import NFLExternalAPI

        api = NFLExternalAPI()
        try:
            if date is None:
                # Fetch current scoreboard without date filter (includes upcoming games)
                data = await api.fetch("scoreboard")
            else:
                # Parse date to ESPN format (YYYYMMDD)
                try:
                    parsed_date = datetime.strptime(date, "%Y-%m-%d")
                    date_str = parsed_date.strftime("%Y%m%d")
                except ValueError:
                    LOGGER.error("Invalid date format: %s", date)
                    return []
                data = await api.fetch("scoreboard", {"dates": date_str})
            return self._parse_scoreboard(data)
        except Exception as e:
            LOGGER.error("Error fetching NFL games for %s: %s", date, e)
            return []
        finally:
            await api.close()

    async def fetch_games_for_week(
        self,
        week: int,
    ) -> list[GameInfo]:
        """Fetch NFL games for a specific week.

        Args:
            week: Week number (1-18 for regular season)

        Returns:
            List of GameInfo objects.
        """
        from dojozero.data.nfl._api import NFLExternalAPI

        api = NFLExternalAPI()
        try:
            data = await api.fetch("scoreboard", {"week": week})
            return self._parse_scoreboard(data)
        except Exception as e:
            LOGGER.error("Error fetching NFL games for week %d: %s", week, e)
            return []
        finally:
            await api.close()

    def _parse_scoreboard(self, data: dict[str, Any]) -> list[GameInfo]:
        """Parse ESPN scoreboard response into GameInfo objects."""
        from dateutil import parser

        scoreboard = data.get("scoreboard", {})
        events = scoreboard.get("events", [])

        games: list[GameInfo] = []
        for event in events:
            event_id = event.get("id", "")
            short_name = event.get("shortName", "")

            competitions = event.get("competitions", [])
            if not competitions:
                continue

            comp = competitions[0]
            status = comp.get("status", {})
            status_type = status.get("type", {})
            status_id = int(status_type.get("id", "1"))
            status_desc = status_type.get("description", "Scheduled")

            # Parse game time
            game_time_utc_str = comp.get("date", "")
            game_time_utc = None
            if game_time_utc_str:
                try:
                    game_time_utc = parser.parse(game_time_utc_str)
                    if game_time_utc.tzinfo is None:
                        game_time_utc = game_time_utc.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            # Get competitors
            competitors = comp.get("competitors", [])
            home_team = TeamInfo(team_id="", name="", tricode="")
            away_team = TeamInfo(team_id="", name="", tricode="")
            for c in competitors:
                team = c.get("team", {})
                team_info = TeamInfo(
                    team_id=team.get("id", ""),
                    name=team.get("displayName", ""),
                    tricode=team.get("abbreviation", ""),
                    score=int(c.get("score", "0") or "0"),
                )
                if c.get("homeAway") == "home":
                    home_team = team_info
                else:
                    away_team = team_info

            # Get venue
            venue = comp.get("venue", {}).get("fullName", "")

            # Get broadcast
            broadcasts = comp.get("broadcasts", [])
            broadcast = ""
            if broadcasts:
                names = broadcasts[0].get("names", [])
                broadcast = ", ".join(names) if names else ""

            # Get odds
            odds_list = comp.get("odds", [])
            odds: dict[str, Any] = {}
            if odds_list:
                o = odds_list[0]
                odds = {
                    "provider": o.get("provider", {}).get("name", ""),
                    "spread": o.get("spread", 0),
                    "overUnder": o.get("overUnder", 0),
                    "homeMoneyLine": o.get("homeTeamOdds", {}).get("moneyLine", 0),
                    "awayMoneyLine": o.get("awayTeamOdds", {}).get("moneyLine", 0),
                }

            game = GameInfo(
                event_id=event_id,
                sport_type="nfl",
                status=status_id,
                status_text=status_desc,
                game_time_utc=game_time_utc,
                home_team=home_team,
                away_team=away_team,
                venue=venue,
                broadcast=broadcast,
                short_name=short_name,
                odds=odds,
            )
            games.append(game)

        return games

    async def get_game_status(
        self, event_id: str, game_date: str | None = None
    ) -> int | None:
        """Get current status of a game.

        Args:
            event_id: ESPN event ID
            game_date: Date of the game (YYYY-MM-DD).

        Returns:
            Game status (1=scheduled, 2=in_progress, 3=finished) or None if not found.
        """
        if game_date:
            games = await self.fetch_games_for_date(game_date)
            for g in games:
                if g.event_id == event_id:
                    return g.status

        # If no date provided or not found, try current scoreboard
        from dojozero.data.nfl._api import NFLExternalAPI

        api = NFLExternalAPI()
        try:
            data = await api.fetch("scoreboard")
            games = self._parse_scoreboard(data)
            for g in games:
                if g.event_id == event_id:
                    return g.status
        except Exception as e:
            LOGGER.warning("Error checking game status for %s: %s", event_id, e)
        finally:
            await api.close()

        return None


__all__ = [
    "GameInfo",
    "NBAGameFetcher",
    "NFLGameFetcher",
    "TeamInfo",
]
