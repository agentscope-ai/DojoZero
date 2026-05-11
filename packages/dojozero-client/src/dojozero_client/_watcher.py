"""Subscription watcher that auto-joins matching trials.

Runs as an asyncio background task inside UnifiedDaemon. Periodically
polls for available trials, matches them against active subscriptions,
and automatically joins those that match.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from dojozero_client._client import DojoClient, GatewayInfo, TrialMetadata
from dojozero_client._config import load_config
from dojozero_client._subscription import Subscription, SubscriptionStore

if TYPE_CHECKING:
    from dojozero_client._daemon import UnifiedDaemon

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 60


class SubscriptionWatcher:
    """Background task that polls for new trials and auto-joins matching ones."""

    def __init__(self, daemon: "UnifiedDaemon", store: SubscriptionStore) -> None:
        self._daemon = daemon
        self._store = store
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._metadata_cache: dict[str, TrialMetadata] = {}
        self._client = DojoClient()

    async def start(self) -> None:
        """Start the watcher background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Subscription watcher started")

    async def stop(self) -> None:
        """Stop the watcher background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Subscription watcher stopped")

    async def _poll_loop(self) -> None:
        """Main loop: sleep -> discover -> match -> join."""
        try:
            while self._running:
                interval = self._get_poll_interval()
                await asyncio.sleep(interval)
                if not self._running:
                    break
                try:
                    await self._poll_once()
                except Exception as e:
                    logger.warning("Subscription watcher poll error: %s", e)
        except asyncio.CancelledError:
            pass

    async def _poll_once(self) -> None:
        """Single poll iteration: discover trials and join matches."""
        active_subs = self._store.get_active()
        if not active_subs:
            return

        try:
            gateways = await self._client.discover_trials()
        except Exception as e:
            logger.debug("Discovery failed during subscription poll: %s", e)
            return

        if not gateways:
            return

        for gw in gateways:
            if not self._running:
                break
            await self._process_gateway(gw, active_subs)

    async def _process_gateway(
        self, gw: GatewayInfo, active_subs: list[Subscription]
    ) -> None:
        """Check a single gateway against all active subscriptions."""
        trial_id = gw.trial_id

        for sub in active_subs:
            if not sub.auto_join:
                continue
            if trial_id in sub.joined_trial_ids:
                continue
            if trial_id in sub.ignored_trial_ids:
                continue

            if sub.max_active_trials > 0:
                active_count = self._count_active_trials(sub)
                if active_count >= sub.max_active_trials:
                    continue

            meta = await self._get_trial_metadata(trial_id)
            if meta is None:
                continue

            if self._matches_subscription(meta, sub):
                success = await self._try_join(trial_id, sub)
                if success:
                    self._store.mark_joined(sub.subscription_id, trial_id)
                    logger.info(
                        "Auto-joined trial %s via subscription %s",
                        trial_id,
                        sub.subscription_id,
                    )
                break  # One subscription match per trial is enough

    def _matches_subscription(self, meta: TrialMetadata, sub: Subscription) -> bool:
        """Check if a trial's metadata matches a subscription's rules."""
        if sub.sport and meta.sport_type.lower() != sub.sport.lower():
            return False

        if sub.teams:
            home_tri = meta.metadata.get("home_team_tricode", "").upper()
            away_tri = meta.metadata.get("away_team_tricode", "").upper()
            home_name = meta.home_team.upper()
            away_name = meta.away_team.upper()
            team_set = {t.upper() for t in sub.teams}

            if not (team_set & {home_tri, away_tri, home_name, away_name}):
                return False

        return True

    async def _try_join(self, trial_id: str, sub: Subscription) -> bool:
        """Attempt to join a trial. Returns True on success."""
        try:
            result = await self._daemon._handle_join(
                trial_id=trial_id,
                filters=sub.filters,
            )
            status = result.get("status", "")
            if status in ("joined", "already_joined"):
                return True
            return False
        except Exception as e:
            logger.warning(
                "Auto-join failed for trial %s (sub %s): %s",
                trial_id,
                sub.subscription_id,
                e,
            )
            return False

    async def _get_trial_metadata(self, trial_id: str) -> TrialMetadata | None:
        """Get trial metadata, using cache to avoid repeated requests."""
        if trial_id in self._metadata_cache:
            return self._metadata_cache[trial_id]

        config = load_config()
        gateway_url = config.get_gateway_url(trial_id)

        try:
            from dojozero_client._transport import GatewayTransport

            transport = GatewayTransport(base_url=gateway_url, timeout=10.0)
            async with transport:
                response = await transport.request("GET", "/trial")
                meta = TrialMetadata.from_dict(response)
                self._metadata_cache[trial_id] = meta
                return meta
        except Exception as e:
            logger.debug("Failed to fetch metadata for trial %s: %s", trial_id, e)
            return None

    def _count_active_trials(self, sub: Subscription) -> int:
        """Count how many of this subscription's joined trials are still active."""
        active = 0
        for trial_id in sub.joined_trial_ids:
            if trial_id in self._daemon._trials:
                handler = self._daemon._trials[trial_id]
                if handler.is_connected:
                    active += 1
        return active

    def _get_poll_interval(self) -> float:
        """Get the shortest poll interval across all active subscriptions."""
        active_subs = self._store.get_active()
        if not active_subs:
            return float(DEFAULT_POLL_INTERVAL)
        return float(min(s.poll_interval_seconds for s in active_subs))

    def clear_metadata_cache(self) -> None:
        """Clear the trial metadata cache (useful after trial ends)."""
        self._metadata_cache.clear()


__all__ = [
    "SubscriptionWatcher",
]
