"""NBA pre-game betting stream initializer for triggering web searches."""

import logging
from typing import Any

from dojozero.data import WebSearchStore
from dojozero.data._streams import DataHubDataStream
from dojozero.data.websearch._events import WebSearchIntent

logger = logging.getLogger(__name__)


class NBAStreamInitializer:
    """Stream initializer that triggers NBA pre-game web searches.

    This initializer generates and executes web search queries for injury reports,
    power rankings, and expert predictions based on team metadata.
    """

    # Available placeholders for query templates
    _AVAILABLE_PLACEHOLDERS = {
        "teams",
        "home_team",
        "away_team",
        "date",
        "home_tricode",
        "away_tricode",
    }

    def __init__(
        self,
        store: WebSearchStore,
        home_team_name: str | None = None,
        away_team_name: str | None = None,
        game_date: str | None = None,
        home_team_tricode: str | None = None,
        away_team_tricode: str | None = None,
        search_queries: list[dict[str, Any]] | None = None,
    ) -> None:
        """Initialize the NBA stream initializer.

        Args:
            store: WebSearchStore instance to use for searches
            home_team_name: Home team full name (e.g., "Los Angeles Lakers")
            away_team_name: Away team full name (e.g., "San Antonio Spurs")
            game_date: Game date string (e.g., "2025-01-15")
            home_team_tricode: Home team tricode (e.g., "LAL")
            away_team_tricode: Away team tricode (e.g., "SAS")
            search_queries: Optional list of custom search queries. Each dict can have:
                - "template": str (optional) - Query template with placeholders
                - "query": str (optional) - Literal query string (if no template)
                - "intent": str (optional) - One of "injury_summary", "power_ranking", "expert_prediction"
                If not provided, queries will be auto-generated.
        """
        self._store = store
        self._home_team_name = home_team_name
        self._away_team_name = away_team_name
        self._game_date = game_date
        self._home_team_tricode = home_team_tricode
        self._away_team_tricode = away_team_tricode
        self._search_queries = search_queries

    async def initialize(self, stream: DataHubDataStream) -> None:
        """Trigger initial web searches to bootstrap the event chain.

        Args:
            stream: The DataHubDataStream instance (not used but required by protocol)
        """
        if self._store is None:
            return

        # Use provided queries if available, otherwise auto-generate
        if self._search_queries:
            queries = self._parse_search_queries(self._search_queries)
        else:
            queries = self._generate_default_queries()

        # Execute searches
        logger.info(
            "stream '%s' triggering initial searches to bootstrap event chain",
            stream.actor_id,
        )
        for query, intent in queries:
            try:
                logger.info(
                    "stream '%s' searching: '%s' (intent: %s)",
                    stream.actor_id,
                    query,
                    intent,
                )
                await self._store.search(query, intent=intent)
            except Exception as e:
                logger.error(
                    "stream '%s' failed to search '%s': %s",
                    stream.actor_id,
                    query,
                    e,
                    exc_info=True,
                )

    def _parse_search_queries(
        self, search_queries: list[dict[str, Any]]
    ) -> list[tuple[str, WebSearchIntent | None]]:
        """Parse search queries from YAML config.

        Supports both template-based queries (with placeholders) and literal queries.

        Args:
            search_queries: List of query dicts. Each can have:
                - "template": str (optional) - Template with placeholders
                - "query": str (optional) - Literal query (if no template)
                - "intent": str (optional) - Intent type

        Returns:
            List of (query_string, intent) tuples
        """
        queries = []
        for query_dict in search_queries:
            # Check for template first, then fall back to literal query
            template = query_dict.get("template")
            literal_query = query_dict.get("query")

            if template:
                # Render template with placeholders
                try:
                    query_str = self._render_template(template)
                except ValueError as e:
                    logger.error(
                        "Failed to render query template '%s': %s. Skipping.",
                        template,
                        e,
                    )
                    continue
            elif literal_query:
                # Use literal query as-is
                if not isinstance(literal_query, str):
                    logger.warning(
                        "Skipping invalid search query (invalid 'query' field): %s",
                        query_dict,
                    )
                    continue
                query_str = literal_query
            else:
                logger.warning(
                    "Skipping invalid search query (missing 'template' or 'query' field): %s",
                    query_dict,
                )
                continue

            # Parse intent
            intent_str = query_dict.get("intent")
            intent: WebSearchIntent | None = None
            if intent_str:
                try:
                    intent = WebSearchIntent(intent_str)
                except (ValueError, TypeError):
                    logger.warning(
                        "Invalid intent '%s' in search query, using None",
                        intent_str,
                    )

            queries.append((query_str, intent))

        return queries

    def _render_template(self, template: str) -> str:
        """Render a query template by replacing placeholders with actual values.

        Args:
            template: Template string with placeholders like {teams}, {home_team}, etc.

        Returns:
            Rendered query string

        Raises:
            ValueError: If template contains unknown placeholders
        """
        import re

        # Find all placeholders in the template
        placeholders = set(re.findall(r"\{(\w+)\}", template))

        # Validate placeholders
        unknown = placeholders - self._AVAILABLE_PLACEHOLDERS
        if unknown:
            available = ", ".join(sorted(self._AVAILABLE_PLACEHOLDERS))
            raise ValueError(
                f"Unknown placeholder(s): {', '.join(sorted(unknown))}. "
                f"Available placeholders: {available}"
            )

        # Build replacement values
        replacements: dict[str, str] = {}

        # {teams} - "Away Team vs Home Team"
        if "teams" in placeholders:
            if self._away_team_name and self._home_team_name:
                replacements["teams"] = (
                    f"{self._away_team_name} vs {self._home_team_name}"
                )
            else:
                replacements["teams"] = ""

        # {home_team} - Home team full name
        if "home_team" in placeholders:
            replacements["home_team"] = self._home_team_name or ""

        # {away_team} - Away team full name
        if "away_team" in placeholders:
            replacements["away_team"] = self._away_team_name or ""

        # {date} - Game date
        if "date" in placeholders:
            replacements["date"] = self._game_date or ""

        # {home_tricode} - Home team tricode
        if "home_tricode" in placeholders:
            replacements["home_tricode"] = self._home_team_tricode or ""

        # {away_tricode} - Away team tricode
        if "away_tricode" in placeholders:
            replacements["away_tricode"] = self._away_team_tricode or ""

        # Replace placeholders in template
        result = template
        for placeholder, value in replacements.items():
            result = result.replace(f"{{{placeholder}}}", value)

        return result

    def _generate_default_queries(self) -> list[tuple[str, WebSearchIntent]]:
        """Generate default queries based on team metadata.

        Returns:
            List of (query_string, intent) tuples
        """
        # Build team context for queries using team names
        teams_str = ""
        if self._home_team_name and self._away_team_name:
            teams_str = f"{self._away_team_name} vs {self._home_team_name}"

        date_str = ""
        if self._game_date:
            date_str = f" on {self._game_date}"

        queries = []

        # Injury report query
        if teams_str:
            injury_query = f"NBA injury updates for {teams_str}{date_str}"
            queries.append((injury_query, WebSearchIntent.INJURY_SUMMARY))
        else:
            queries.append(("NBA injury updates", WebSearchIntent.INJURY_SUMMARY))

        # Power ranking query
        queries.append(("NBA power rankings", WebSearchIntent.POWER_RANKING))

        # Expert prediction query
        if teams_str:
            prediction_query = f"NBA expert predictions for {teams_str}{date_str}"
            queries.append((prediction_query, WebSearchIntent.EXPERT_PREDICTION))
        else:
            queries.append(
                ("NBA expert predictions", WebSearchIntent.EXPERT_PREDICTION)
            )

        return queries
