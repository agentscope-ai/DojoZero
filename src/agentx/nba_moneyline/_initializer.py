"""NBA pre-game betting stream initializer for triggering web searches."""

import logging
from typing import Any

from agentx.data import WebSearchStore
from agentx.data._streams import DataHubDataStream, StreamInitializer
from agentx.data.websearch._events import WebSearchIntent

LOGGER = logging.getLogger("agentx.nba_moneyline.initializer")


class NBAStreamInitializer:
    """Stream initializer that triggers NBA pre-game web searches.
    
    This initializer generates and executes web search queries for injury reports,
    power rankings, and expert predictions based on team metadata.
    """
    
    def __init__(
        self,
        store: WebSearchStore,
        home_team_name: str | None = None,
        away_team_name: str | None = None,
        game_date: str | None = None,
    ) -> None:
        """Initialize the NBA stream initializer.
        
        Args:
            store: WebSearchStore instance to use for searches
            home_team_name: Home team full name (e.g., "Los Angeles Lakers")
            away_team_name: Away team full name (e.g., "San Antonio Spurs")
            game_date: Game date string (e.g., "2025-01-15")
        """
        self._store = store
        self._home_team_name = home_team_name
        self._away_team_name = away_team_name
        self._game_date = game_date
    
    async def initialize(self, stream: DataHubDataStream) -> None:
        """Trigger initial web searches to bootstrap the event chain.
        
        Args:
            stream: The DataHubDataStream instance (not used but required by protocol)
        """
        if self._store is None:
            return
        
        # Build team context for queries using team names
        teams_str = ""
        if self._home_team_name and self._away_team_name:
            teams_str = f"{self._away_team_name} vs {self._home_team_name}"
        
        date_str = ""
        if self._game_date:
            date_str = f" on {self._game_date}"
        
        # Generate queries with team info embedded
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
            queries.append(("NBA expert predictions", WebSearchIntent.EXPERT_PREDICTION))
        
        # Execute searches
        LOGGER.info(
            "stream '%s' triggering initial searches to bootstrap event chain",
            stream.actor_id,
        )
        for query, intent in queries:
            try:
                LOGGER.info(
                    "stream '%s' searching: '%s' (intent: %s)",
                    stream.actor_id,
                    query,
                    intent,
                )
                await self._store.search(query, intent=intent)
            except Exception as e:
                LOGGER.error(
                    "stream '%s' failed to search '%s': %s",
                    stream.actor_id,
                    query,
                    e,
                    exc_info=True,
                )
