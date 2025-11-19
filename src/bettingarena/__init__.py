"""
BettingArena - A realtime benchmark system for AI agents in betting.

This package provides a platform for AI agents to place bets
using virtual dollars, with comprehensive tracking and performance analysis.
"""

import importlib.metadata
from typing import Final

try:
    _version = importlib.metadata.version(__name__)
except importlib.metadata.PackageNotFoundError:
    _version = "0.0.0"  # Fallback for development mode
__version__: Final[str] = _version
__author__ = "BettingArena Team"
