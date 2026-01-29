"""Polymarket-specific event types.

The old dataclass-based OddsUpdateEvent has been replaced by the unified
Pydantic OddsUpdateEvent in dojozero.data._models. This module re-exports
it for backward compatibility.
"""

from dojozero.data._models import OddsUpdateEvent

__all__ = ["OddsUpdateEvent"]
