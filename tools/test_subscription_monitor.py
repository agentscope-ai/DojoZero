"""Subscription integration test monitor.

Runs alongside the daemon for 1-2 days and records:
  - Every trial that appears on the server (via /api/gateways)
  - Whether the subscription watcher auto-joined each trial
  - Per-trial participation coverage
  - A final report written to logs/subscription_test_report.json

Usage:
    # Terminal 1 – backend
    dojo0 serve --host 0.0.0.0 --port 8001 \\
        --trial-source trial_sources/test/nba.yaml

    # Terminal 2 – client daemon (background)
    dojozero-agent config --dashboard-url http://localhost:8001
    dojozero-agent config --api-key <your-key>
    dojozero-agent subscribe --sport nba --days 2 --forever
    dojozero-agent daemon -b

    # Terminal 3 – this monitor
    python tools/test_subscription_monitor.py \\
        --url http://localhost:8001 \\
        --poll 60 \\
        --duration 48

Requirements:
    pip install httpx rich
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx", file=sys.stderr)
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table

    _has_rich = True
except ImportError:
    _has_rich = False

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
_log_file = LOG_DIR / f"subscription_test_{_ts}.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("sub_monitor")

REPORT_FILE = LOG_DIR / f"subscription_test_report_{_ts}.json"
DOJOZERO_CONFIG = Path.home() / ".dojozero"
SUBSCRIPTIONS_FILE = DOJOZERO_CONFIG / "subscriptions.json"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class TrialRecord:
    """Tracks a single trial from first discovery to join confirmation."""

    def __init__(self, trial_id: str) -> None:
        self.trial_id = trial_id
        self.first_seen: str = datetime.now(timezone.utc).isoformat()
        self.metadata: dict[str, Any] = {}
        self.joined: bool = False
        self.joined_at: str | None = None
        self.matched_subscription: str | None = None
        self.join_latency_seconds: float | None = None

    def mark_joined(self, sub_id: str) -> None:
        if not self.joined:
            self.joined = True
            now = datetime.now(timezone.utc)
            self.joined_at = now.isoformat()
            self.matched_subscription = sub_id
            first_seen_dt = datetime.fromisoformat(self.first_seen)
            if first_seen_dt.tzinfo is None:
                first_seen_dt = first_seen_dt.replace(tzinfo=timezone.utc)
            self.join_latency_seconds = (now - first_seen_dt).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "first_seen": self.first_seen,
            "metadata": self.metadata,
            "joined": self.joined,
            "joined_at": self.joined_at,
            "matched_subscription": self.matched_subscription,
            "join_latency_seconds": self.join_latency_seconds,
        }


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class SubscriptionTestMonitor:
    def __init__(
        self,
        server_url: str,
        poll_interval: int = 60,
        duration_hours: float = 48,
    ) -> None:
        self._url = server_url.rstrip("/")
        self._poll_interval = poll_interval
        self._duration_seconds = duration_hours * 3600
        self._trials: dict[str, TrialRecord] = {}
        self._running = False
        self._start_time: datetime | None = None
        self._poll_count = 0
        self._client = httpx.AsyncClient(timeout=15.0)

        if _has_rich:
            self._console = Console()
        else:
            self._console = None  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True
        self._start_time = datetime.now(timezone.utc)
        logger.info("=" * 60)
        logger.info("Subscription test monitor started")
        logger.info("  Server : %s", self._url)
        logger.info("  Poll   : every %ds", self._poll_interval)
        logger.info("  Duration: %.1fh", self._duration_seconds / 3600)
        logger.info("  Log    : %s", _log_file)
        logger.info("  Report : %s", REPORT_FILE)
        logger.info("=" * 60)

        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGINT, self._handle_stop)
        loop.add_signal_handler(signal.SIGTERM, self._handle_stop)

        try:
            while self._running:
                elapsed = (
                    datetime.now(timezone.utc) - self._start_time
                ).total_seconds()
                if elapsed >= self._duration_seconds:
                    logger.info("Test duration reached (%.1fh). Stopping.", elapsed / 3600)
                    break

                await self._poll_once()
                self._print_status()
                await asyncio.sleep(self._poll_interval)
        finally:
            await self._client.aclose()
            self._write_report()
            logger.info("Monitor finished. Report: %s", REPORT_FILE)

    def _handle_stop(self) -> None:
        logger.info("Received stop signal.")
        self._running = False

    # ------------------------------------------------------------------
    # Core poll
    # ------------------------------------------------------------------

    async def _poll_once(self) -> None:
        self._poll_count += 1
        logger.debug("Poll #%d", self._poll_count)

        # 1. Discover available trials from server
        gateways = await self._discover_trials()
        for gw in gateways:
            trial_id = gw.get("trial_id", "")
            if not trial_id:
                continue
            if trial_id not in self._trials:
                rec = TrialRecord(trial_id)
                self._trials[trial_id] = rec
                logger.info("NEW trial discovered: %s", trial_id)

                # Try to fetch metadata
                meta = await self._fetch_trial_metadata(trial_id)
                if meta:
                    rec.metadata = meta
                    sport = meta.get("sportType", "?")
                    home = meta.get("metadata", {}).get("home_team_tricode", meta.get("homeTeam", "?"))
                    away = meta.get("metadata", {}).get("away_team_tricode", meta.get("awayTeam", "?"))
                    logger.info(
                        "  Metadata: %s | %s @ %s", sport, away, home
                    )

        # 2. Read subscription state from disk (~/.dojozero/subscriptions.json)
        joined_by_sub = self._read_joined_from_subscriptions()

        # 3. Mark trials as joined if subscription store says so
        for sub_id, trial_ids in joined_by_sub.items():
            for trial_id in trial_ids:
                if trial_id in self._trials:
                    rec = self._trials[trial_id]
                    if not rec.joined:
                        rec.mark_joined(sub_id)
                        logger.info(
                            "JOINED: trial=%s via sub=%s (latency=%.0fs)",
                            trial_id,
                            sub_id,
                            rec.join_latency_seconds or 0,
                        )
                else:
                    # Trial was joined but we didn't see it during discovery
                    # (it may have appeared and been joined before this monitor started)
                    rec = TrialRecord(trial_id)
                    rec.joined = True
                    rec.joined_at = datetime.now(timezone.utc).isoformat()
                    rec.matched_subscription = sub_id
                    self._trials[trial_id] = rec
                    logger.info(
                        "JOINED (pre-existing): trial=%s via sub=%s",
                        trial_id,
                        sub_id,
                    )

        # 4. Check for trials visible but NOT yet joined
        unjoin = [tid for tid, r in self._trials.items() if not r.joined]
        if unjoin:
            logger.debug("Trials seen but not yet joined (%d): %s", len(unjoin), unjoin)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _discover_trials(self) -> list[dict[str, Any]]:
        try:
            r = await self._client.get(f"{self._url}/api/gateways")
            r.raise_for_status()
            data = r.json()
            return data.get("gateways", [])
        except Exception as e:
            logger.warning("Discovery failed: %s", e)
            return []

    async def _fetch_trial_metadata(self, trial_id: str) -> dict[str, Any] | None:
        try:
            r = await self._client.get(f"{self._url}/api/trials/{trial_id}/trial")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.debug("Metadata fetch failed for %s: %s", trial_id, e)
            return None

    # ------------------------------------------------------------------
    # Subscription store reader
    # ------------------------------------------------------------------

    def _read_joined_from_subscriptions(self) -> dict[str, list[str]]:
        """Read joined_trial_ids from the local subscriptions.json."""
        if not SUBSCRIPTIONS_FILE.exists():
            return {}
        try:
            data = json.loads(SUBSCRIPTIONS_FILE.read_text())
            result: dict[str, list[str]] = {}
            for s in data.get("subscriptions", []):
                sub_id = s.get("subscription_id", "")
                joined = s.get("joined_trial_ids", [])
                if sub_id and joined:
                    result[sub_id] = joined
            return result
        except Exception as e:
            logger.warning("Could not read subscriptions.json: %s", e)
            return {}

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _print_status(self) -> None:
        total = len(self._trials)
        joined = sum(1 for r in self._trials.values() if r.joined)
        elapsed_h = (
            (datetime.now(timezone.utc) - self._start_time).total_seconds() / 3600
            if self._start_time
            else 0
        )
        coverage = (joined / total * 100) if total else 0.0

        if _has_rich and self._console:
            t = Table(title=f"Subscription Test — Poll #{self._poll_count} | {elapsed_h:.1f}h elapsed")
            t.add_column("Trial ID", style="cyan")
            t.add_column("Sport")
            t.add_column("Matchup")
            t.add_column("Joined", style="green")
            t.add_column("Latency")
            for trial_id, r in self._trials.items():
                sport = r.metadata.get("sportType", "?")
                home_tri = r.metadata.get("metadata", {}).get("home_team_tricode", "?")
                away_tri = r.metadata.get("metadata", {}).get("away_team_tricode", "?")
                joined_str = "✓" if r.joined else "—"
                latency_str = (
                    f"{r.join_latency_seconds:.0f}s" if r.join_latency_seconds else "—"
                )
                t.add_row(trial_id[:24], sport, f"{away_tri}@{home_tri}", joined_str, latency_str)
            self._console.print(t)
            self._console.print(
                f"[bold]Coverage: {joined}/{total} ({coverage:.0f}%)[/bold]"
            )
        else:
            logger.info(
                "Status: poll=%d elapsed=%.1fh trials=%d joined=%d coverage=%.0f%%",
                self._poll_count,
                elapsed_h,
                total,
                joined,
                coverage,
            )

    def _write_report(self) -> None:
        total = len(self._trials)
        joined = sum(1 for r in self._trials.values() if r.joined)
        latencies = [
            r.join_latency_seconds
            for r in self._trials.values()
            if r.join_latency_seconds is not None
        ]
        avg_latency = sum(latencies) / len(latencies) if latencies else None

        report = {
            "test_start": self._start_time.isoformat() if self._start_time else "",
            "test_end": datetime.now(timezone.utc).isoformat(),
            "server_url": self._url,
            "poll_interval_seconds": self._poll_interval,
            "total_polls": self._poll_count,
            "summary": {
                "trials_discovered": total,
                "trials_joined": joined,
                "coverage_pct": round(joined / total * 100, 1) if total else 0.0,
                "avg_join_latency_seconds": round(avg_latency, 1) if avg_latency else None,
                "missed_trials": [
                    tid for tid, r in self._trials.items() if not r.joined
                ],
            },
            "trials": {tid: r.to_dict() for tid, r in self._trials.items()},
        }

        REPORT_FILE.write_text(json.dumps(report, indent=2))
        logger.info("=" * 60)
        logger.info("FINAL REPORT")
        logger.info("  Trials discovered : %d", total)
        logger.info("  Trials joined     : %d", joined)
        logger.info(
            "  Coverage          : %.1f%%", report["summary"]["coverage_pct"]
        )
        if avg_latency:
            logger.info("  Avg join latency  : %.1fs", avg_latency)
        missed = report["summary"]["missed_trials"]
        if missed:
            logger.warning("  MISSED trials (%d): %s", len(missed), missed)
        else:
            logger.info("  All discovered trials were joined!")
        logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor subscription auto-join over a multi-day test run."
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DOJOZERO_DASHBOARD_URL", "http://localhost:8001"),
        help="Backend server URL (default: http://localhost:8001)",
    )
    parser.add_argument(
        "--poll",
        type=int,
        default=60,
        help="Poll interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=48,
        help="Test duration in hours (default: 48)",
    )
    args = parser.parse_args()

    monitor = SubscriptionTestMonitor(
        server_url=args.url,
        poll_interval=args.poll,
        duration_hours=args.duration,
    )
    asyncio.run(monitor.run())


if __name__ == "__main__":
    main()
