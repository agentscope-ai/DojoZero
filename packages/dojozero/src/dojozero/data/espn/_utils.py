"""Shared ESPN utilities."""

from typing import Any


def safe_score(comp: dict[str, Any] | None) -> int:
    """Extract an integer score from an ESPN competitor dict.

    ESPN may return ``"score"`` as a plain string/int **or** as a nested dict
    like ``{"value": "110", "displayValue": "110"}``.  Handle both.
    """
    if comp is None:
        return 0
    raw = comp.get("score", 0)
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("displayValue", 0))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0
