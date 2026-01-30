"""Game context for web search template rendering."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GameContext:
    """Lightweight game context for rendering search query templates.

    Constructed from BettingTrialMetadata at trial build time and passed
    to event classes for query construction.
    """

    sport: str = ""
    home_team: str = ""
    away_team: str = ""
    home_tricode: str = ""
    away_tricode: str = ""
    game_date: str = ""
    game_id: str = ""  # ESPN game ID for populating event.game_id
    home_team_id: str = ""  # ESPN team ID for home team
    away_team_id: str = ""  # ESPN team ID for away team
    season_year: int = 0
    season_type: str = ""  # "regular", "postseason", "preseason"

    @property
    def teams(self) -> str:
        """Return 'Away Team vs Home Team' string."""
        if self.away_team and self.home_team:
            return f"{self.away_team} vs {self.home_team}"
        return ""

    def render_template(self, template: str) -> str:
        """Render a query template by replacing placeholders with context values.

        Supported placeholders: {sport}, {teams}, {home_team}, {away_team},
        {date}, {home_tricode}, {away_tricode}.

        Args:
            template: Template string with placeholders.

        Returns:
            Rendered query string.
        """
        return template.format(
            sport=self.sport.upper() if self.sport else "",
            teams=self.teams,
            home_team=self.home_team,
            away_team=self.away_team,
            date=self.game_date,
            home_tricode=self.home_tricode,
            away_tricode=self.away_tricode,
        )


__all__ = ["GameContext"]
