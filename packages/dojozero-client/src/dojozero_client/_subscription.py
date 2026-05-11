"""Subscription model and persistent storage for auto-join rules.

Subscriptions define rules for automatically joining future trials
(e.g., "join all NBA trials for the next 7 days"). They are stored
in ~/.dojozero/subscriptions.json and loaded by the daemon's watcher.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dojozero_client._config import CONFIG_DIR

logger = logging.getLogger(__name__)

SUBSCRIPTIONS_FILE_NAME = "subscriptions.json"


def _default_filters() -> list[str]:
    return ["event.*", "odds.*"]


def _default_list() -> list[str]:
    return []


@dataclass
class Subscription:
    """A rule that triggers auto-join for matching trials."""

    subscription_id: str
    enabled: bool = True
    sport: str = ""
    teams: list[str] = field(default_factory=_default_list)
    starts_at: str | None = None
    expires_at: str | None = None
    poll_interval_seconds: int = 60
    auto_join: bool = True
    max_active_trials: int = 0
    filters: list[str] = field(default_factory=_default_filters)
    joined_trial_ids: list[str] = field(default_factory=_default_list)
    ignored_trial_ids: list[str] = field(default_factory=_default_list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Subscription:
        return cls(
            subscription_id=data.get("subscription_id", ""),
            enabled=data.get("enabled", True),
            sport=data.get("sport", ""),
            teams=data.get("teams", []),
            starts_at=data.get("starts_at"),
            expires_at=data.get("expires_at"),
            poll_interval_seconds=data.get("poll_interval_seconds", 60),
            auto_join=data.get("auto_join", True),
            max_active_trials=data.get("max_active_trials", 0),
            filters=data.get("filters", ["event.*", "odds.*"]),
            joined_trial_ids=data.get("joined_trial_ids", []),
            ignored_trial_ids=data.get("ignored_trial_ids", []),
            created_at=data.get("created_at", ""),
        )

    @classmethod
    def create(
        cls,
        sport: str,
        teams: list[str] | None = None,
        days: int | None = None,
        forever: bool = False,
        poll_interval_seconds: int = 60,
        max_active_trials: int = 0,
        filters: list[str] | None = None,
    ) -> Subscription:
        """Create a new subscription with auto-generated ID and timestamps."""
        now = datetime.now(timezone.utc)
        expires_at: str | None = None
        if days and not forever:
            expires_at = (now + timedelta(days=days)).isoformat()
        elif not forever and not days:
            expires_at = (now + timedelta(days=7)).isoformat()

        return cls(
            subscription_id=f"sub-{uuid.uuid4().hex[:8]}",
            enabled=True,
            sport=sport.lower(),
            teams=[t.upper() for t in (teams or [])],
            starts_at=now.isoformat(),
            expires_at=expires_at,
            poll_interval_seconds=poll_interval_seconds,
            auto_join=True,
            max_active_trials=max_active_trials,
            filters=filters or ["event.*", "odds.*"],
            joined_trial_ids=[],
            ignored_trial_ids=[],
            created_at=now.isoformat(),
        )

    def is_active(self, now: datetime | None = None) -> bool:
        """Check if subscription is currently active (enabled and not expired)."""
        if not self.enabled:
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        if self.starts_at:
            starts = datetime.fromisoformat(self.starts_at)
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=timezone.utc)
            if now < starts:
                return False
        if self.expires_at:
            expires = datetime.fromisoformat(self.expires_at)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now > expires:
                return False
        return True


class SubscriptionStore:
    """Persistent storage for subscriptions, profile-aware."""

    def __init__(self, profile_dir: Path | None = None) -> None:
        self._dir = profile_dir or CONFIG_DIR
        self._file = self._dir / SUBSCRIPTIONS_FILE_NAME

    def load(self) -> list[Subscription]:
        """Load all subscriptions from disk."""
        if not self._file.exists():
            return []
        try:
            data = json.loads(self._file.read_text())
            return [Subscription.from_dict(s) for s in data.get("subscriptions", [])]
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load subscriptions: %s", e)
            return []

    def save(self, subscriptions: list[Subscription]) -> None:
        """Save all subscriptions to disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {"subscriptions": [s.to_dict() for s in subscriptions]}
        self._file.write_text(json.dumps(data, indent=2))

    def add(self, sub: Subscription) -> None:
        """Add a subscription and persist."""
        subs = self.load()
        subs.append(sub)
        self.save(subs)

    def remove(self, subscription_id: str) -> bool:
        """Remove a subscription by ID. Returns True if found and removed."""
        subs = self.load()
        original_len = len(subs)
        subs = [s for s in subs if s.subscription_id != subscription_id]
        if len(subs) == original_len:
            return False
        self.save(subs)
        return True

    def get(self, subscription_id: str) -> Subscription | None:
        """Get a single subscription by ID."""
        for s in self.load():
            if s.subscription_id == subscription_id:
                return s
        return None

    def update(self, sub: Subscription) -> bool:
        """Update an existing subscription in place."""
        subs = self.load()
        for i, s in enumerate(subs):
            if s.subscription_id == sub.subscription_id:
                subs[i] = sub
                self.save(subs)
                return True
        return False

    def mark_joined(self, subscription_id: str, trial_id: str) -> None:
        """Record that a trial was joined under a subscription."""
        subs = self.load()
        for s in subs:
            if s.subscription_id == subscription_id:
                if trial_id not in s.joined_trial_ids:
                    s.joined_trial_ids.append(trial_id)
                break
        self.save(subs)

    def get_active(self, now: datetime | None = None) -> list[Subscription]:
        """Get only active (enabled + not expired) subscriptions."""
        return [s for s in self.load() if s.is_active(now)]


__all__ = [
    "Subscription",
    "SubscriptionStore",
    "SUBSCRIPTIONS_FILE_NAME",
]
