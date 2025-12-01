"""Data infrastructure types: DataFacts and DataEvents.

DataFacts represent current state snapshots (pull-based).
DataEvents represent changes/deltas (push-based streaming).
"""

from ._facts import (
    DataFact,
    GameScoreFact,
    GameStatusFact,
    OddsFact,
    PlayerStatusFact,
    SearchResultFact,
    TeamStatsFact,
)
from ._events import (
    DataEvent,
    GameStatusEvent,
    GoogleSearchResultEvent,
    InjuryEvent,
    NewsEvent,
    OddsChangeEvent,
    PlayByPlayEvent,
    ScoreboardSnapshotEvent,
    TeamStatsEvent,
)

__all__ = [
    # Base classes
    "DataFact",
    "DataEvent",
    # Fact types
    "GameScoreFact",
    "OddsFact",
    "GameStatusFact",
    "TeamStatsFact",
    "PlayerStatusFact",
    "SearchResultFact",
    # Event types
    "PlayByPlayEvent",
    "ScoreboardSnapshotEvent",
    "OddsChangeEvent",
    "InjuryEvent",
    "GameStatusEvent",
    "NewsEvent",
    "TeamStatsEvent",
    "GoogleSearchResultEvent",
]

