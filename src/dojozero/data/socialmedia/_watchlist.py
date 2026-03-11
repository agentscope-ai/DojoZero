"""
Social Media Watchlist Registry for NBA and NFL Game Analysis.

Maintains curated lists of official team accounts, beat reporters, and
betting analysts. Provides lookup by team tricode and constructs
per-game watchlists. Supports runtime updates.

Usage:
    registry = NBAWatchlistRegistry()
    watchlist = registry.build_game_watchlist("PHI", "CLE")

    # Update accounts
    registry.update_team_account("PHI", "sixers_new")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class SocialAccount:
    """A single Twitter/X account to monitor."""

    username: str
    team_tricode: Optional[str] = None  # None for league-wide accounts
    description: str = ""

    @property
    def profile_url(self) -> str:
        return f"https://x.com/{self.username}"


@dataclass
class GameWatchlist:
    """Assembled watchlist for a specific game matchup."""

    home_tricode: str
    away_tricode: str
    accounts: list[SocialAccount] = field(default_factory=list)

    @property
    def all_usernames(self) -> set[str]:
        return {a.username.lower() for a in self.accounts}

    def summary(self) -> str:
        lines = [f"Game Watchlist: {self.home_tricode} vs {self.away_tricode}"]
        lines.append(f"Total accounts: {len(self.accounts)}")
        usernames = [f"@{a.username}" for a in self.accounts]
        if usernames:
            lines.append(f"  Accounts: {', '.join(usernames)}")
        return "\n".join(lines)


class BaseWatchlistRegistry:
    """Base class for sport-specific watchlist registries."""

    TEAM_ACCOUNTS: dict[str, str] = {}
    BEAT_REPORTERS: dict[str, list[str]] = {}
    BETTING_ANALYSTS: list[str] = []
    _TRICODE_ALIASES: dict[str, str] = {}

    def _normalize_tricode(self, tricode: str) -> str:
        """Normalize team tricode, handling aliases."""
        tri = tricode.upper().strip()
        return self._TRICODE_ALIASES.get(tri, tri)

    def get_team_account(self, tricode: str) -> Optional[SocialAccount]:
        """Get official team account for a tricode."""
        tri = self._normalize_tricode(tricode)
        username = self.TEAM_ACCOUNTS.get(tri)
        if not username:
            return None
        return SocialAccount(
            username=username,
            team_tricode=tri,
            description=f"{tri} official team account",
        )

    def get_beat_reporters(self, tricode: str) -> list[SocialAccount]:
        """Get beat reporters for a team."""
        tri = self._normalize_tricode(tricode)
        reporters = self.BEAT_REPORTERS.get(tri, [])
        return [
            SocialAccount(
                username=r,
                team_tricode=tri,
                description=f"{tri} beat reporter",
            )
            for r in reporters
        ]

    def get_betting_analysts(self) -> list[SocialAccount]:
        """Get all betting analysts."""
        return [
            SocialAccount(
                username=u,
                description="Betting/analytics analyst",
            )
            for u in self.BETTING_ANALYSTS
        ]

    def build_game_watchlist(
        self,
        home_tricode: str,
        away_tricode: str,
    ) -> GameWatchlist:
        """
        Assemble a deduplicated watchlist for a game.
        """
        home = self._normalize_tricode(home_tricode)
        away = self._normalize_tricode(away_tricode)
        watchlist = GameWatchlist(home_tricode=home, away_tricode=away)

        seen_usernames: set[str] = set()

        def _add(accounts: list[SocialAccount]):
            for acct in accounts:
                key = acct.username.lower()
                if key not in seen_usernames:
                    seen_usernames.add(key)
                    watchlist.accounts.append(acct)

        # Beat reporters (both teams)
        _add(self.get_beat_reporters(home))
        _add(self.get_beat_reporters(away))

        # Official team accounts (both teams)
        for tri in (home, away):
            team_acct = self.get_team_account(tri)
            if team_acct:
                _add([team_acct])

        # Betting analysts
        _add(self.get_betting_analysts())

        return watchlist

    def update_team_account(self, tricode: str, username: str) -> None:
        """Update or add a team account."""
        tri = self._normalize_tricode(tricode)
        self.TEAM_ACCOUNTS[tri] = username

    def update_beat_reporter(
        self, tricode: str, username: str, add: bool = True
    ) -> None:
        """Update beat reporters for a team."""
        tri = self._normalize_tricode(tricode)
        if tri not in self.BEAT_REPORTERS:
            self.BEAT_REPORTERS[tri] = []

        if add and username not in self.BEAT_REPORTERS[tri]:
            self.BEAT_REPORTERS[tri].append(username)
        elif not add and username in self.BEAT_REPORTERS[tri]:
            self.BEAT_REPORTERS[tri].remove(username)

    def update_betting_analyst(self, username: str, add: bool = True) -> None:
        """Update betting analysts list."""
        if add and username not in self.BETTING_ANALYSTS:
            self.BETTING_ANALYSTS.append(username)
        elif not add and username in self.BETTING_ANALYSTS:
            self.BETTING_ANALYSTS.remove(username)


class NBAWatchlistRegistry(BaseWatchlistRegistry):
    """
    Central registry of NBA social media accounts organized by team and role.

    Call `build_game_watchlist(home_tri, away_tri)` to get a deduplicated,
    priority-sorted watchlist for any matchup.
    """

    # ── Official Team Accounts ──────────────────────────────────────────
    TEAM_ACCOUNTS: dict[str, str] = {
        "ATL": "ATLHawks",
        "BOS": "celtics",
        "BKN": "BrooklynNets",
        "CHA": "hornets",
        "CHI": "chicagobulls",
        "CLE": "cavs",
        "DAL": "dallasmavs",
        "DEN": "nuggets",
        "DET": "DetroitPistons",
        "GSW": "warriors",
        "HOU": "HoustonRockets",
        "IND": "Pacers",
        "LAC": "LAClippers",
        "LAL": "Lakers",
        "MEM": "memgrizz",
        "MIA": "MiamiHEAT",
        "MIL": "Bucks",
        "MIN": "Timberwolves",
        "NOP": "PelicansNBA",
        "NYK": "nyknicks",
        "OKC": "okcthunder",
        "ORL": "OrlandoMagic",
        "PHI": "sixers",
        "PHX": "Suns",
        "POR": "trailblazers",
        "SAC": "SacramentoKings",
        "SAS": "spurs",
        "TOR": "Raptors",
        "UTA": "utahjazz",
        "WAS": "WashWizards",
    }

    # ── Beat Reporters (per team) ───────────────────────────────────────
    # Primary beat writers who break injury/lineup/practice news.
    # Source: FiddlesPicks/Substack Oct 2024; verify once per season.
    BEAT_REPORTERS: dict[str, list[str]] = {
        # Eastern Conference
        "ATL": ["KLChouinard", "williamslaurenl"],
        "BOS": ["ByJayKing", "ChrisForsberg_"],
        "BKN": ["erikslater_", "NYPost_Lewis"],
        "CHA": ["rodboone"],
        "CHI": ["KCJHoop"],
        "CLE": ["ChrisFedor"],
        "DET": ["CotyDavis_24"],
        "IND": ["DustinDopirak", "ScottAgness"],
        "MIA": ["IraHeatBeat", "BradyHawk305"],
        "MIL": ["eric_nehm"],
        "NYK": ["FredKatz", "StevePopper"],
        "ORL": ["therealBeede"],
        "PHI": ["PompeyOnSixers", "KyleNeubeck"],
        "TOR": ["JLew1050"],
        "WAS": ["JoshuaBRobbins"],
        # Western Conference
        "DAL": ["townbrad", "GrantAfseth"],
        "DEN": ["BennettDurando", "msinger"],
        "GSW": ["JoeVirayNBA", "anthonyVslater"],
        "HOU": ["Jonathan_Feigen"],
        "LAC": ["TomerAzarly"],
        "LAL": ["LakersReporter", "jovanbuha"],
        "MEM": ["DamichaelC", "DrewHill_DM"],
        "MIN": ["ChristopherHine", "JonKrawczynski"],
        "NOP": ["Jim_Eichenhofer", "WillGuillory"],
        "OKC": ["BrandonRahbar", "jxlorenzi"],
        "PHX": ["GeraldBourguet", "DuaneRankin"],
        "POR": ["jwquick", "CHold"],
        "SAC": ["James_HamNBA", "JandersonSacBee"],
        "SAS": ["JMcDonald_SAEN", "PaulGarciaNBA"],
        "UTA": ["andyblarsen", "NBASarah"],
    }

    # ── Betting / Analytics Analysts (top 3) ─────────────────────────────
    BETTING_ANALYSTS: list[str] = [
        "ActionNetworkHQ",  # The Action Network
        "VSiNLive",  # VSiN
        "ESPNBet",  # ESPN Betting
    ]

    # ── Tricode aliases (handle alternate codes) ────────────────────────
    _TRICODE_ALIASES: dict[str, str] = {
        "NO": "NOP",
        "SA": "SAS",
        "GS": "GSW",
        "NY": "NYK",
        "PHO": "PHX",
        "BRK": "BKN",
        "CHO": "CHA",
    }


class NFLWatchlistRegistry(BaseWatchlistRegistry):
    """
    Central registry of NFL social media accounts organized by team and role.
    """

    # ── Official Team Accounts ──────────────────────────────────────────
    TEAM_ACCOUNTS: dict[str, str] = {
        "ARI": "AZCardinals",
        "ATL": "AtlantaFalcons",
        "BAL": "Ravens",
        "BUF": "BuffaloBills",
        "CAR": "Panthers",
        "CHI": "ChicagoBears",
        "CIN": "Bengals",
        "CLE": "Browns",
        "DAL": "dallascowboys",
        "DEN": "Broncos",
        "DET": "Lions",
        "GB": "packers",
        "HOU": "HoustonTexans",
        "IND": "Colts",
        "JAX": "Jaguars",
        "KC": "Chiefs",
        "LV": "Raiders",
        "LAR": "RamsNFL",
        "LAC": "Chargers",
        "MIA": "MiamiDolphins",
        "MIN": "Vikings",
        "NE": "Patriots",
        "NO": "Saints",
        "NYG": "Giants",
        "NYJ": "nyjets",
        "PHI": "Eagles",
        "PIT": "steelers",
        "SF": "49ers",
        "SEA": "Seahawks",
        "TB": "Buccaneers",
        "TEN": "Titans",
        "WAS": "Commanders",
    }

    # ── Beat Reporters (per team) ───────────────────────────────────────
    # Note: NFL beat reporters can be added here as needed
    BEAT_REPORTERS: dict[str, list[str]] = {}

    # ── Betting / Analytics Analysts ────────────────────────────────────
    BETTING_ANALYSTS: list[str] = [
        "ActionNetworkHQ",  # The Action Network
        "VSiNLive",  # VSiN
        "ESPNBet",  # ESPN Betting
    ]

    # ── Tricode aliases ─────────────────────────────────────────────────
    _TRICODE_ALIASES: dict[str, str] = {}


# Convenience functions to get registries
def get_nba_registry() -> NBAWatchlistRegistry:
    """Get the NBA watchlist registry instance."""
    return NBAWatchlistRegistry()


def get_nfl_registry() -> NFLWatchlistRegistry:
    """Get the NFL watchlist registry instance."""
    return NFLWatchlistRegistry()


def get_registry(sport: str) -> BaseWatchlistRegistry:
    """Get the appropriate registry for a sport."""
    sport_upper = sport.upper()
    if sport_upper in ("NBA", "BASKETBALL"):
        return get_nba_registry()
    elif sport_upper in ("NFL", "FOOTBALL"):
        return get_nfl_registry()
    else:
        raise ValueError(f"Unsupported sport: {sport}")
