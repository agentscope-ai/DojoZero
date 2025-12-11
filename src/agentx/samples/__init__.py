"""Sample scenarios package."""

# Import sample modules to trigger builder registration on package import.
from . import bounded_random  # noqa: F401
from . import bounded_random_buffered  # noqa: F401
from . import nba_pregame_betting  # noqa: F401

__all__ = ["bounded_random", "bounded_random_buffered", "nba_pregame_betting"]
