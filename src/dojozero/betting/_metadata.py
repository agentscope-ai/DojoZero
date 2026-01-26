"""Typed metadata definitions for betting trials.

This module provides TypedDict classes for type-safe trial metadata,
reducing the risk of key name mismatches between producers (trial builders)
and consumers (store factories, context builders).

Usage:
    from dojozero.betting import BaseBettingTrialMetadata

    # In trial builder
    metadata: BaseBettingTrialMetadata = {
        "sample": "nba",
        "sport_type": "nba",
        "espn_game_id": "401810490",
        "hub_id": "nba_hub",
        ...
    }
"""

from typing import TypedDict


class BaseBettingTrialMetadata(TypedDict, total=False):
    """Common metadata fields for all betting trials.

    This TypedDict defines the contract between trial builders and consumers
    (store factories, context builders). Using TypedDict provides:
    - IDE autocomplete for metadata keys
    - Type checker catches typos and missing keys
    - Self-documenting interface

    Fields:
        sample: Trial type identifier (e.g., "nba", "nfl-moneyline")
        sport_type: Sport type ("nba" or "nfl")
        builder_name: Trial builder name (auto-added by registry)
        espn_game_id: ESPN event/game ID
        game_date: Game date in YYYY-MM-DD format

        home_tricode: Home team code (e.g., "LAL", "KC")
        away_tricode: Away team code (e.g., "BOS", "SF")
        home_team_name: Full home team name (e.g., "Los Angeles Lakers")
        away_team_name: Full away team name (e.g., "Boston Celtics")

        hub_id: DataHub identifier
        persistence_file: Path to event persistence JSONL file
        event_types: List of event types for the trial
        store_types: List of store types to create (e.g., ["nba", "websearch", "polymarket"])

        market_url: Optional Polymarket market URL
    """

    # Trial type and builder info
    sample: str
    sport_type: str
    builder_name: str

    # Game identification
    espn_game_id: str
    game_date: str

    # Team info (unified naming - always use "tricode")
    home_tricode: str
    away_tricode: str
    home_team_name: str
    away_team_name: str

    # Hub configuration
    hub_id: str
    persistence_file: str
    event_types: list[str]
    store_types: list[str]

    # Polymarket
    market_url: str


# Keys that consumers can expect in metadata
# Useful for validation and documentation
REQUIRED_METADATA_KEYS = frozenset(
    [
        "sample",
        "espn_game_id",
        "hub_id",
        "persistence_file",
    ]
)

TEAM_INFO_KEYS = frozenset(
    [
        "home_tricode",
        "away_tricode",
        "home_team_name",
        "away_team_name",
        "game_date",
    ]
)


__all__ = [
    "BaseBettingTrialMetadata",
    "REQUIRED_METADATA_KEYS",
    "TEAM_INFO_KEYS",
]
