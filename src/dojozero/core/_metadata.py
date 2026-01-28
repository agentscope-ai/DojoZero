"""Base metadata types for trial specifications.

This module provides the foundational metadata types that all trial types extend.
It defines the TypeVar used for generic TrialSpec typing.

Usage:
    from dojozero.core import BaseTrialMetadata, MetadataT

    @dataclass
    class MyTrialMetadata(BaseTrialMetadata):
        my_field: str
"""

from dataclasses import dataclass
from typing import TypeVar


@dataclass(slots=True)
class BaseTrialMetadata:
    """Base metadata common to all trial types.

    All trial metadata classes should inherit from this base.

    Attributes:
        hub_id: DataHub identifier for the trial
        persistence_file: Path to event persistence JSONL file
        store_types: Tuple of store type names to create (e.g., ("nba", "websearch"))
    """

    hub_id: str
    persistence_file: str
    store_types: tuple[str, ...]


# TypeVar for generic TrialSpec and StoreFactory
MetadataT = TypeVar("MetadataT", bound=BaseTrialMetadata)


__all__ = [
    "BaseTrialMetadata",
    "MetadataT",
]
