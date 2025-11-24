"""Sample environments package."""

# Import bounded_random to trigger builder registration on package import.
from . import bounded_random  # noqa: F401

__all__ = ["bounded_random"]
