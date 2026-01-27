"""Typed metadata definitions for betting trials.

This module provides dataclass-based metadata for type-safe trial configuration,
reducing the risk of key name mismatches between producers (trial builders)
and consumers (store factories, context builders).

Usage:
    from dojozero.betting import BettingTrialMetadata

    # In trial builder - IDE will catch missing required fields
    metadata = BettingTrialMetadata(
        sample="nba",
        sport_type="nba",
        espn_game_id="401810490",
        hub_id="nba_hub",
        persistence_file="outputs/events.jsonl",
        store_types=("nba", "websearch", "polymarket"),
        event_types=("event.game_update", "event.odds_update"),
    )
"""

from dataclasses import dataclass
from typing import Literal

from dojozero.core._metadata import BaseTrialMetadata


@dataclass(slots=True, frozen=True)
class BettingTrialMetadata(BaseTrialMetadata):
    """Typed metadata for betting trials (NBA, NFL).

    This dataclass defines the contract between trial builders and consumers
    (store factories, context builders). Using dataclass provides:
    - IDE autocomplete for metadata fields
    - Type checker catches typos and missing required fields
    - Constructor validation for required fields
    - Immutability (frozen=True)

    Attributes:
        sample: Trial type identifier (e.g., "nba", "nfl-moneyline")
        sport_type: Sport type ("nba" or "nfl")
        espn_game_id: ESPN event/game ID
        event_types: Tuple of event types for the trial

        home_tricode: Home team code (e.g., "LAL", "KC")
        away_tricode: Away team code (e.g., "BOS", "SF")
        home_team_name: Full home team name (e.g., "Los Angeles Lakers")
        away_team_name: Full away team name (e.g., "Boston Celtics")
        game_date: Game date in YYYY-MM-DD format

        market_url: Optional Polymarket market URL

        nba_poll_intervals: Optional NBA store poll intervals
        nfl_poll_intervals: Optional NFL store poll intervals
        polymarket_poll_intervals: Optional Polymarket store poll intervals
    """

    # Required fields (in addition to base class fields)
    sample: str
    sport_type: Literal["nba", "nfl"]
    espn_game_id: str
    event_types: tuple[str, ...]

    # Team info (required - populated from ESPN API via get_game_info_by_id_async)
    home_tricode: str
    away_tricode: str
    home_team_name: str
    away_team_name: str
    game_date: str

    # Polymarket (optional)
    market_url: str | None = None

    # Poll interval overrides (optional)
    nba_poll_intervals: dict[str, float] | None = None
    nfl_poll_intervals: dict[str, float] | None = None
    polymarket_poll_intervals: dict[str, float] | None = None


__all__ = [
    "BettingTrialMetadata",
]
