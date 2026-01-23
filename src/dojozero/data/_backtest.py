"""BacktestCoordinator: Orchestrates backtesting from files to DataHub."""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dojozero.data._hub import DataHub

logger = logging.getLogger(__name__)


class BacktestCoordinator:
    """Orchestrates backtesting from files to DataHub.

    Reads events from persistence files and replays them through DataHub
    to agents, simulating live data flow. Supports speed control and progress tracking.
    """

    def __init__(self, data_hub: DataHub, backtest_file: Path | str | None = None):
        """Initialize backtest coordinator.

        Args:
            data_hub: DataHub instance to send events to
            backtest_file: Optional path to backtest file (can be set later)
        """
        self.data_hub = data_hub
        self.backtest_file = Path(backtest_file) if backtest_file else None
        self._running = False
        self._speed_up = 1.0
        self._max_sleep = 20.0
        self._progress_callback: Callable[[int, int], None] | None = None

    async def start(self, backtest_file: Path | str | None = None) -> None:
        """Start backtest from a file.

        Args:
            backtest_file: Path to backtest file (uses instance backtest_file if not provided)
        """
        if backtest_file:
            self.backtest_file = Path(backtest_file)

        if not self.backtest_file or not self.backtest_file.exists():
            raise FileNotFoundError(f"Backtest file not found: {self.backtest_file}")

        self._running = True
        await self.data_hub.start_backtest(str(self.backtest_file))
        logger.info("Started backtest from file: %s", self.backtest_file)

    def set_speed(self, speed_up: float = 1.0, max_sleep: float = 20.0) -> None:
        """Set backtest speed parameters.

        Args:
            speed_up: Speed multiplier (1.0 = real-time, 2.0 = 2x speed, etc.)
            max_sleep: Maximum sleep time in seconds between events (caps long delays)
        """
        if speed_up <= 0:
            raise ValueError(f"Speed-up must be positive, got: {speed_up}")
        if max_sleep <= 0:
            raise ValueError(f"Max sleep must be positive, got: {max_sleep}")

        self._speed_up = speed_up
        self._max_sleep = max_sleep

    def set_progress_callback(self, callback: Callable[[int, int], None]) -> None:
        """Set callback for progress updates.

        Args:
            callback: Function called with (current_count, total_count) during backtest
        """
        self._progress_callback = callback

    async def run_all(self) -> None:
        """Run backtest for all events from the file at configured speed."""
        if not self._running:
            await self.start()

        await self._run_with_speed_control()

    async def run_next(self) -> Any:
        """Run next event in backtest.

        Returns:
            Next event or None if backtest is complete
        """
        if not self._running:
            await self.start()

        return await self.data_hub.backtest_next()

    async def _run_with_speed_control(self) -> None:
        """Run all events with speed control and progress tracking."""
        if not self.data_hub._backtest_mode or not self.data_hub._backtest_events:
            logger.warning("Hub is not in backtest mode or has no events")
            return

        total_events = len(self.data_hub._backtest_events)
        if total_events == 0:
            logger.info("No events to backtest")
            return

        start_time = datetime.now(timezone.utc)
        last_event_time: datetime | None = None

        logger.info(
            "Running backtest: %d events at %.1fx speed (max sleep: %.1fs)",
            total_events,
            self._speed_up,
            self._max_sleep,
        )

        event_count = 0
        while True:
            event = await self.data_hub.backtest_next()
            if event is None:
                break

            event_count += 1

            # Calculate delay based on speed (only if we have a previous event)
            if last_event_time is not None and self._speed_up > 0:
                # Calculate time difference between events
                time_diff = (event.timestamp - last_event_time).total_seconds()
                # Adjust for speed
                delay = time_diff / self._speed_up
                # Cap delay at max_sleep to avoid excessively long waits
                delay = min(delay, self._max_sleep)

                if delay > 0:
                    await asyncio.sleep(delay)

            last_event_time = event.timestamp

            # Progress callback
            if self._progress_callback:
                self._progress_callback(event_count, total_events)

            # Log progress every 10% or every 100 events, whichever is more frequent
            if event_count % max(1, min(100, total_events // 10)) == 0:
                progress_pct = (event_count / total_events) * 100
                logger.info(
                    "Backtest progress: %d/%d events (%.1f%%)",
                    event_count,
                    total_events,
                    progress_pct,
                )

        # Final summary
        end_time = datetime.now(timezone.utc)
        elapsed = (end_time - start_time).total_seconds()
        events_per_sec = event_count / elapsed if elapsed > 0 else 0

        logger.info(
            "Backtest complete: %d events in %.1f seconds (%.1f events/sec)",
            event_count,
            elapsed,
            events_per_sec,
        )

    def stop(self) -> None:
        """Stop backtest."""
        self._running = False
        self.data_hub.stop_backtest()
        logger.info("Stopped backtest")


# Backward compatibility alias (deprecated)
ReplayCoordinator = BacktestCoordinator
