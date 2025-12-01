"""Data processors for transforming events into facts.

This module provides:
- Aggregators: Convert events to facts (stateful and stateless)
- LLM Processors: Transform events using LLM processing
"""

from ._aggregators import (
    AGGREGATOR_REGISTRY,
    GameStatusEventToFactAggregator,
    OddsChangeToOddsAggregator,
    PlayByPlayToScoreAggregator,
    StatefulAggregator,
    StatelessAggregator,
    StatelessOddsAggregator,
    StatelessScoreAggregator,
    StatelessTeamStatsAggregator,
    create_game_status_aggregator,
    create_odds_aggregator,
    create_score_aggregator,
    create_stateless_odds_aggregator,
    create_stateless_score_aggregator,
    create_stateless_team_stats_aggregator,
    get_aggregator,
)
from ._processors import (
    LLMSearchResultProcessor,
    create_llm_search_processor,
)

__all__ = [
    # Aggregators
    "StatelessAggregator",
    "StatefulAggregator",
    "PlayByPlayToScoreAggregator",
    "OddsChangeToOddsAggregator",
    "GameStatusEventToFactAggregator",
    "StatelessScoreAggregator",
    "StatelessOddsAggregator",
    "StatelessTeamStatsAggregator",
    "create_score_aggregator",
    "create_odds_aggregator",
    "create_game_status_aggregator",
    "create_stateless_score_aggregator",
    "create_stateless_odds_aggregator",
    "create_stateless_team_stats_aggregator",
    "get_aggregator",
    "AGGREGATOR_REGISTRY",
    # Processors
    "LLMSearchResultProcessor",
    "create_llm_search_processor",
]

