"""Game Discovery for DojoZero Dashboard Server.

Provides unified interfaces for fetching game information from
NBA API and ESPN API (for NFL).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from dojozero.data._game_info import GameInfo, TeamInfo, VenueInfo
from dojozero.data.espn._api import ESPNExternalAPI
from dojozero.data.nfl._api import NFLExternalAPI

from dojozero.utils import utc_to_us_date

from ._types import BroadcastDataDict, OddsDataDict, TeamDataDict, VenueDataDict

LOGGER = logging.getLogger("dojozero.game_discovery")


def _espn_us_game_day_today() -> str:
    """Calendar date for ESPN scoreboard `dates=` (US Eastern game day)."""
    return utc_to_us_date(datetime.now(timezone.utc))


def _espn_us_game_day_today_and_yesterday() -> tuple[str, str]:
    """Today and previous US game day, for scoreboard lookups without a known date."""
    today = _espn_us_game_day_today()
    d = datetime.strptime(today, "%Y-%m-%d").date()
    yesterday = (d - timedelta(days=1)).isoformat()
    return today, yesterday


def _parse_team_data(competitor: dict[str, Any]) -> TeamDataDict:
    """Parse team data from ESPN competitor object.

    Args:
        competitor: ESPN competitor dict from competition.competitors

    Returns:
        TeamDataDict with team data formatted for TeamInfo model.
    """
    team = competitor.get("team", {})
    records = competitor.get("records", [])
    record = records[0].get("summary", "") if records else ""

    return TeamDataDict(
        teamId=team.get("id", ""),
        displayName=team.get("displayName", ""),
        teamTricode=team.get("abbreviation", ""),
        score=competitor.get("score", "0"),
        teamCity=team.get("location", ""),
        shortDisplayName=team.get("shortDisplayName", ""),
        color=team.get("color", ""),
        alternateColor=team.get("alternateColor", ""),
        logo=team.get("logo", ""),
        record=record,
    )


def _parse_venue_data(venue_data: dict[str, Any]) -> VenueDataDict:
    """Parse venue data from ESPN venue object.

    Args:
        venue_data: ESPN venue dict from competition.venue

    Returns:
        VenueDataDict with venue data formatted for VenueInfo model.
    """
    venue_address = venue_data.get("address", {})
    return VenueDataDict(
        venueId=str(venue_data.get("id", "")),
        name=venue_data.get("fullName", ""),
        city=venue_address.get("city", ""),
        state=venue_address.get("state", ""),
        indoor=venue_data.get("indoor", True),
    )


def _parse_broadcast_data(
    broadcasts_raw: list[dict[str, Any]],
) -> tuple[list[BroadcastDataDict], str]:
    """Parse broadcast data from ESPN broadcasts array.

    Args:
        broadcasts_raw: ESPN broadcasts list from competition.broadcasts

    Returns:
        Tuple of (broadcasts list, broadcast summary string).
    """
    broadcasts: list[BroadcastDataDict] = []
    broadcast_names: list[str] = []
    for b in broadcasts_raw:
        market = b.get("market", "")
        names = b.get("names", [])
        broadcasts.append(BroadcastDataDict(market=market, names=names))
        if names:
            broadcast_names.extend(names)
    broadcast = ", ".join(broadcast_names) if broadcast_names else ""
    return broadcasts, broadcast


def _parse_odds_data(odds_list: list[dict[str, Any]]) -> OddsDataDict:
    """Parse odds data from ESPN odds array.

    Args:
        odds_list: ESPN odds list from competition.odds

    Returns:
        OddsDataDict with odds data.
    """
    if not odds_list:
        return OddsDataDict()
    o = odds_list[0]
    return OddsDataDict(
        provider=o.get("provider", {}).get("name", ""),
        spread=o.get("spread", 0),
        overUnder=o.get("overUnder", 0),
        homeMoneyLine=o.get("homeTeamOdds", {}).get("moneyLine", 0),
        awayMoneyLine=o.get("awayTeamOdds", {}).get("moneyLine", 0),
    )


# Mapping from ESPN season type ID to readable string
_SEASON_TYPE_MAP = {
    1: "preseason",
    2: "regular",
    3: "postseason",
    4: "offseason",
}


def _parse_espn_event(event: dict[str, Any], sport_type: str) -> GameInfo | None:
    """Parse a single ESPN event into a GameInfo object.

    Args:
        event: ESPN event dict from scoreboard.events
        sport_type: Sport type string (e.g., "nba", "nfl")

    Returns:
        GameInfo object or None if event has no competitions.
    """
    competitions = event.get("competitions", [])
    if not competitions:
        return None

    comp = competitions[0]
    status = comp.get("status", {})
    status_type = status.get("type", {})

    # Parse competitors into home/away team data
    competitors = comp.get("competitors", [])
    home_team_data: TeamDataDict = TeamDataDict()
    away_team_data: TeamDataDict = TeamDataDict()
    for c in competitors:
        team_data = _parse_team_data(c)
        if c.get("homeAway") == "home":
            home_team_data = team_data
        else:
            away_team_data = team_data

    # Parse other competition data
    venue = _parse_venue_data(comp.get("venue", {}))
    broadcasts, broadcast = _parse_broadcast_data(comp.get("broadcasts", []))
    odds = _parse_odds_data(comp.get("odds", []))

    # Get season info
    season = event.get("season", {})
    season_type_id = season.get("type", 0)

    # Build game data dict for Pydantic validation
    game_data = {
        "gameId": event.get("id", ""),
        "sport_type": sport_type,
        "gameStatus": int(status_type.get("id", "1")),
        "gameStatusText": status_type.get("description", "Scheduled"),
        "gameTimeUTC": comp.get("date", ""),
        "homeTeam": home_team_data,
        "awayTeam": away_team_data,
        "venue": venue,
        "broadcasts": broadcasts,
        "broadcast": broadcast,
        "name": event.get("name", ""),
        "shortName": event.get("shortName", ""),
        "odds": odds,
        "period": status.get("period", 0),
        "gameClock": status.get("displayClock", ""),
        "attendance": comp.get("attendance", 0),
        "neutralSite": comp.get("neutralSite", False),
        "seasonYear": season.get("year", 0),
        "seasonType": _SEASON_TYPE_MAP.get(season_type_id, ""),
    }

    return GameInfo.model_validate(game_data)


def _parse_espn_scoreboard(data: dict[str, Any], sport_type: str) -> list[GameInfo]:
    """Parse ESPN scoreboard response into GameInfo objects.

    This is a shared parser for ESPN API scoreboard responses, which have
    the same format across different sports (NBA, NFL, etc.).

    Args:
        data: ESPN API response with "scoreboard" key
        sport_type: Sport type string (e.g., "nba", "nfl")

    Returns:
        List of GameInfo objects.
    """
    scoreboard = data.get("scoreboard", {})
    events = scoreboard.get("events", [])

    games: list[GameInfo] = []
    for event in events:
        game = _parse_espn_event(event, sport_type)
        if game is not None:
            games.append(game)

    return games


class NBAGameFetcher:
    """Fetches NBA game information from ESPN API."""

    async def fetch_games_for_date(
        self,
        date: str | None = None,
    ) -> list[GameInfo]:
        """Fetch NBA games for a specific date.

        Args:
            date: Date in YYYY-MM-DD format. If None, uses today's US Eastern
                  game day (matches ESPN scoreboard `dates=`, avoids UTC midnight
                  skew in Docker).

        Returns:
            List of GameInfo objects.
        """
        api = ESPNExternalAPI(sport="basketball", league="nba")
        try:
            # Default to US game calendar so UTC-hosted servers still see "tonight's" slate
            if date is None:
                date = _espn_us_game_day_today()

            # Parse date to ESPN format (YYYYMMDD)
            try:
                parsed_date = datetime.strptime(date, "%Y-%m-%d")
                date_str = parsed_date.strftime("%Y%m%d")
            except ValueError:
                LOGGER.error("Invalid date format: %s", date)
                return []
            data = await api.fetch("scoreboard", {"dates": date_str})
            return _parse_espn_scoreboard(data, "nba")
        except Exception as e:
            LOGGER.error("Error fetching NBA games for %s: %s", date, e)
            return []
        finally:
            await api.close()

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
            game_id: ESPN event ID
            game_date: Date of the game (YYYY-MM-DD). If None, searches recent dates.

        Returns:
            Game status (1=scheduled, 2=in_progress, 3=finished, 4=postponed, 5=cancelled)
            or None if not found.
        """
        result = await self.get_game_status_info(game_id, game_date)
        return result[0] if result else None

    async def get_game_status_info(
        self, game_id: str, game_date: str | None = None
    ) -> tuple[int, str] | None:
        """Get current status and status text of a game.

        Args:
            game_id: ESPN event ID
            game_date: Date of the game (YYYY-MM-DD). If None, searches recent dates.

        Returns:
            Tuple of (status_code, status_text) or None if not found.
            Status codes: 1=scheduled, 2=in_progress, 3=finished, 4=postponed, 5=cancelled
        """

        def _map_status(game: GameInfo) -> tuple[int, str]:
            """Maps game status text to internal status codes."""
            status_text = game.status_text.lower()
            if "postponed" in status_text:
                return (4, game.status_text)
            if "canceled" in status_text or "cancelled" in status_text:
                return (5, game.status_text)
            return (game.status, game.status_text)

        # Build list of dates to check
        dates_to_check: list[str | None] = []
        if game_date:
            dates_to_check.append(game_date)
        else:
            us_today, us_yesterday = _espn_us_game_day_today_and_yesterday()
            dates_to_check.extend([us_today, us_yesterday])

        for date in dates_to_check:
            games = await self.fetch_games_for_date(date)
            for g in games:
                if g.game_id == game_id:
                    return _map_status(g)

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
            return _parse_espn_scoreboard(data, "nfl")
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
        api = NFLExternalAPI()
        try:
            data = await api.fetch("scoreboard", {"week": week})
            return _parse_espn_scoreboard(data, "nfl")
        except Exception as e:
            LOGGER.error("Error fetching NFL games for week %d: %s", week, e)
            return []
        finally:
            await api.close()

    async def get_game_status(
        self, event_id: str, game_date: str | None = None
    ) -> int | None:
        """Get current status of a game.

        Args:
            event_id: ESPN event ID
            game_date: Date of the game (YYYY-MM-DD).

        Returns:
            Game status (1=scheduled, 2=in_progress, 3=finished, 4=postponed, 5=cancelled)
            or None if not found.
        """
        result = await self.get_game_status_info(event_id, game_date)
        return result[0] if result else None

    async def get_game_status_info(
        self, event_id: str, game_date: str | None = None
    ) -> tuple[int, str] | None:
        """Get current status and status text of a game.

        Args:
            event_id: ESPN event ID
            game_date: Date of the game (YYYY-MM-DD).

        Returns:
            Tuple of (status_code, status_text) or None if not found.
            Status codes: 1=scheduled, 2=in_progress, 3=finished, 4=postponed, 5=cancelled
        """

        def _map_status(game: GameInfo) -> tuple[int, str]:
            status_text = game.status_text.lower()
            if "postponed" in status_text:
                return (4, game.status_text)  # STATUS_POSTPONED
            if "canceled" in status_text or "cancelled" in status_text:
                return (5, game.status_text)  # STATUS_CANCELLED
            return (game.status, game.status_text)

        if game_date:
            games = await self.fetch_games_for_date(game_date)
            for g in games:
                if g.game_id == event_id:
                    return _map_status(g)

        # If no date provided or not found, try current scoreboard
        api = NFLExternalAPI()
        try:
            data = await api.fetch("scoreboard")
            games = _parse_espn_scoreboard(data, "nfl")
            for g in games:
                if g.game_id == event_id:
                    return _map_status(g)
        except Exception as e:
            LOGGER.warning("Error checking game status for %s: %s", event_id, e)
        finally:
            await api.close()

        return None


class NCAAGameFetcher:
    """Fetches NCAA Men's Basketball game information from ESPN API."""

    async def fetch_games_for_date(
        self,
        date: str | None = None,
    ) -> list[GameInfo]:
        """Fetch NCAA basketball games for a specific date.

        Args:
            date: Date in YYYY-MM-DD format. If None, uses today's US Eastern
                  game day (matches ESPN scoreboard `dates=`).

        Returns:
            List of GameInfo objects.
        """
        api = ESPNExternalAPI(sport="basketball", league="mens-college-basketball")
        try:
            if date is None:
                date = _espn_us_game_day_today()

            try:
                parsed_date = datetime.strptime(date, "%Y-%m-%d")
                date_str = parsed_date.strftime("%Y%m%d")
            except ValueError:
                LOGGER.error("Invalid date format: %s", date)
                return []
            data = await api.fetch("scoreboard", {"dates": date_str})
            return _parse_espn_scoreboard(data, "ncaa")
        except Exception as e:
            LOGGER.error("Error fetching NCAA games for %s: %s", date, e)
            return []
        finally:
            await api.close()

    async def fetch_games_for_date_range(
        self,
        start_date: str,
        end_date: str,
    ) -> list[GameInfo]:
        """Fetch NCAA basketball games for a date range.

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
            game_id: ESPN event ID
            game_date: Date of the game (YYYY-MM-DD). If None, searches recent dates.

        Returns:
            Game status (1=scheduled, 2=in_progress, 3=finished, 4=postponed, 5=cancelled)
            or None if not found.
        """
        result = await self.get_game_status_info(game_id, game_date)
        return result[0] if result else None

    async def get_game_status_info(
        self, game_id: str, game_date: str | None = None
    ) -> tuple[int, str] | None:
        """Get current status and status text of a game.

        Args:
            game_id: ESPN event ID
            game_date: Date of the game (YYYY-MM-DD). If None, searches recent dates.

        Returns:
            Tuple of (status_code, status_text) or None if not found.
        """

        def _map_status(game: GameInfo) -> tuple[int, str]:
            status_text = game.status_text.lower()
            if "postponed" in status_text:
                return (4, game.status_text)
            if "canceled" in status_text or "cancelled" in status_text:
                return (5, game.status_text)
            return (game.status, game.status_text)

        dates_to_check: list[str | None] = []
        if game_date:
            dates_to_check.append(game_date)
        else:
            us_today, us_yesterday = _espn_us_game_day_today_and_yesterday()
            dates_to_check.extend([us_today, us_yesterday])

        for date in dates_to_check:
            games = await self.fetch_games_for_date(date)
            for g in games:
                if g.game_id == game_id:
                    return _map_status(g)

        return None


__all__ = [
    "GameInfo",
    "NBAGameFetcher",
    "NCAAGameFetcher",
    "NFLGameFetcher",
    "TeamInfo",
    "VenueInfo",
]
