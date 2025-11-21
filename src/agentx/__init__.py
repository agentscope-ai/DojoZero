"""
AgentX package.
"""

import importlib.metadata
from typing import Final

try:
    _version = importlib.metadata.version(__name__)
except importlib.metadata.PackageNotFoundError:
    _version = "0.0.0"  # Fallback for development mode
__version__: Final[str] = _version
__author__ = "AgentX Team"
