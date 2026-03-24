"""Base metadata types for trial specifications.

This module provides the foundational metadata types that all trial types extend.
It defines the TypeVar used for generic TrialSpec typing.

Usage:
    from dojozero.core import BaseTrialMetadata, MetadataT

    @dataclass
    class MyTrialMetadata(BaseTrialMetadata):
        my_field: str
"""

from dataclasses import dataclass, fields
from typing import Any, TypeVar


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

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like access for backward compatibility with code that
        treats metadata as a plain dict."""
        if key in {f.name for f in fields(self)}:
            return getattr(self, key)
        return default

    def __contains__(self, key: object) -> bool:
        """Support ``key in metadata`` checks."""
        return isinstance(key, str) and key in {f.name for f in fields(self)}

    def __getitem__(self, key: str) -> Any:
        """Support ``metadata["key"]`` access."""
        if key in {f.name for f in fields(self)}:
            return getattr(self, key)
        raise KeyError(key)

    def __iter__(self):
        """Support ``for key in metadata`` iteration over field names."""
        return iter(f.name for f in fields(self))

    def items(self):
        """Support ``dict(metadata.items())`` and dict-like iteration."""
        return ((f.name, getattr(self, f.name)) for f in fields(self))

    def keys(self):
        """Return field names."""
        return (f.name for f in fields(self))


# TypeVar for generic TrialSpec and StoreFactory
MetadataT = TypeVar("MetadataT", bound=BaseTrialMetadata)


__all__ = [
    "BaseTrialMetadata",
    "MetadataT",
]
