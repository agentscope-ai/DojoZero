#!/usr/bin/env python3
"""Robust external agent with reconnection and error handling.

This example demonstrates:
- Automatic reconnection on SSE disconnects
- Fallback to polling when SSE is unavailable
- Graceful error handling
- State persistence across reconnections

Usage:
    # First, start a trial with gateway enabled:
    dojo0 run --params your_trial.yaml --enable-gateway --gateway-port 8080

    # Then run this agent:
    python robust_agent.py --gateway http://localhost:8080 --agent-id robust-agent
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from dojozero_client import (
    DojoClient,
    EventEnvelope,
    TrialConnection,
    StreamDisconnectedError,
    ConnectionError,
    StaleReferenceError,
    InsufficientBalanceError,
    BettingClosedError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class AgentState:
    """Persistent agent state across reconnections."""

    last_sequence: int = 0
    bets_placed: int = 0
    total_wagered: float = 0.0
    wins: int = 0
    losses: int = 0
    reconnect_count: int = 0
    start_time: datetime = field(default_factory=datetime.now)


async def discover_gateway(
    dashboard_urls: list[str] | None, trial_id: str
) -> str | None:
    """Discover gateway URL for a trial from dashboard servers.

    Args:
        dashboard_urls: List of dashboard server URLs (None = use config/env defaults)
        trial_id: Trial ID to find

    Returns:
        Gateway URL or None if not found
    """
    client = DojoClient(dashboard_urls=dashboard_urls)
    url_count = len(dashboard_urls) if dashboard_urls else "config"
    logger.info("Discovering trial '%s' from %s dashboard(s)...", trial_id, url_count)

    try:
        gateways = await client.discover_trials()
        if not gateways:
            logger.error("No trials available")
            return None

        matching = [g for g in gateways if g.trial_id == trial_id]
        if not matching:
            logger.error(
                "Trial '%s' not found. Available: %s",
                trial_id,
                [g.trial_id for g in gateways],
            )
            return None

        gateway_url = matching[0].url
        logger.info("Found gateway: %s", gateway_url)
        return gateway_url
    except Exception as e:
        logger.error("Discovery failed: %s", e)
        return None


class RobustBettingAgent:
    """A robust agent with reconnection handling."""

    def __init__(
        self,
        gateway_url: str,
        agent_id: str,
        bet_amount: float = 10.0,
        bet_threshold: float = 0.55,
        max_reconnect_attempts: int = 10,
        reconnect_delay: float = 5.0,
        poll_interval: float = 2.0,
        api_key: str | None = None,
    ):
        """Initialize the agent.

        Args:
            gateway_url: Gateway URL
            agent_id: Unique agent identifier
            bet_amount: Amount to bet each time
            bet_threshold: Probability threshold for betting
            max_reconnect_attempts: Max consecutive reconnection attempts
            reconnect_delay: Delay between reconnection attempts (seconds)
            poll_interval: Polling interval when SSE unavailable (seconds)
            api_key: API key for authentication (from dojo0 agents add)
        """
        self.gateway_url = gateway_url
        self.agent_id = agent_id
        self.bet_amount = bet_amount
        self.bet_threshold = bet_threshold
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = reconnect_delay
        self.poll_interval = poll_interval
        self.api_key = api_key
        self.state = AgentState()
        self._client = DojoClient()
        self._running = True

    async def run(self):
        """Run the agent with automatic reconnection."""
        consecutive_failures = 0

        while self._running and consecutive_failures < self.max_reconnect_attempts:
            try:
                await self._run_session()
                consecutive_failures = 0  # Reset on successful session
            except StreamDisconnectedError as e:
                self.state.reconnect_count += 1
                consecutive_failures += 1
                logger.warning(
                    "SSE stream disconnected (attempt %d/%d): %s",
                    consecutive_failures,
                    self.max_reconnect_attempts,
                    e,
                )
                if consecutive_failures < self.max_reconnect_attempts:
                    logger.info("Reconnecting in %.1fs...", self.reconnect_delay)
                    await asyncio.sleep(self.reconnect_delay)
            except ConnectionError as e:
                consecutive_failures += 1
                logger.error(
                    "Connection error (attempt %d/%d): %s",
                    consecutive_failures,
                    self.max_reconnect_attempts,
                    e,
                )
                if consecutive_failures < self.max_reconnect_attempts:
                    logger.info("Retrying in %.1fs...", self.reconnect_delay)
                    await asyncio.sleep(self.reconnect_delay)
            except asyncio.CancelledError:
                logger.info("Agent cancelled")
                break
            except Exception as e:
                logger.exception("Unexpected error: %s", e)
                consecutive_failures += 1
                if consecutive_failures < self.max_reconnect_attempts:
                    await asyncio.sleep(self.reconnect_delay)

        if consecutive_failures >= self.max_reconnect_attempts:
            logger.error("Max reconnection attempts reached, giving up")

        self._print_summary()

    async def _run_session(self):
        """Run a single session (until disconnect)."""
        logger.info("Connecting to trial at %s", self.gateway_url)

        async with self._client.connect_trial(
            gateway_url=self.gateway_url,
            agent_id=self.agent_id,
            initial_balance=1000.0,
            api_key=self.api_key,
        ) as trial:
            metadata = await trial.get_trial_metadata()
            logger.info(
                "Connected to trial '%s': %s vs %s",
                metadata.trial_id,
                metadata.away_team,
                metadata.home_team,
            )

            balance = await trial.get_balance()
            logger.info("Current balance: %s", balance.balance)

            # Try SSE streaming first, fall back to polling
            try:
                await self._stream_events(trial)
            except StreamDisconnectedError:
                logger.warning("SSE disconnected, trying polling fallback")
                await self._poll_events(trial)
                raise  # Re-raise to trigger reconnection

    async def _stream_events(self, trial: TrialConnection):
        """Stream events via SSE."""
        logger.info("Starting SSE event stream...")
        event_count = 0
        async for event in trial.events():
            if not self._running:
                break
            event_count += 1
            event_type = event.payload.get("event_type", "unknown")
            # Log first 10 events at INFO to show snapshot
            if event_count <= 10:
                logger.info(
                    "Event #%d [seq=%d]: %s",
                    event_count,
                    event.sequence,
                    event_type,
                )
            await self._handle_event(trial, event)

    async def _poll_events(self, trial: TrialConnection):
        """Poll for events (fallback when SSE unavailable)."""
        logger.info("Starting polling mode (interval: %.1fs)", self.poll_interval)

        while self._running:
            try:
                events = await trial.poll_events(
                    since=self.state.last_sequence,
                    limit=50,
                )

                for event in events:
                    await self._handle_event(trial, event)

                await asyncio.sleep(self.poll_interval)

            except ConnectionError:
                raise  # Let outer loop handle reconnection
            except Exception as e:
                logger.error("Polling error: %s", e)
                await asyncio.sleep(self.poll_interval)

    async def _handle_event(self, trial: TrialConnection, event: EventEnvelope):
        """Handle a single event."""
        # Update last seen sequence
        if event.sequence > self.state.last_sequence:
            self.state.last_sequence = event.sequence

        event_type = event.payload.get("event_type", "unknown")

        # Only process game events
        if not event_type.startswith("event."):
            return

        # Check if we should bet
        odds = await self._get_odds_safe(trial)
        if odds is None or not odds.betting_open:
            return

        # Skip if event sequence is too stale (likely snapshot replay)
        # The server rejects bets with reference_sequence more than 10 behind current
        current_seq = getattr(odds, "sequence", None)
        if current_seq is not None and event.sequence < current_seq - 10:
            logger.debug(
                "Skipping stale event (seq=%d, current=%d)",
                event.sequence,
                current_seq,
            )
            return

        # Simple betting logic: bet on favorite
        # Skip if odds not available yet
        if odds.home_probability is None or odds.away_probability is None:
            return
        if odds.home_probability > self.bet_threshold:
            await self._place_bet_safe(trial, "home", event.sequence)
        elif odds.away_probability > self.bet_threshold:
            await self._place_bet_safe(trial, "away", event.sequence)

    async def _get_odds_safe(self, trial: TrialConnection):
        """Get odds with error handling."""
        try:
            return await trial.get_current_odds()
        except Exception as e:
            logger.debug("Failed to get odds: %s", e)
            return None

    async def _place_bet_safe(
        self,
        trial: TrialConnection,
        selection: str,
        reference_sequence: int,
    ):
        """Place a bet with error handling."""
        try:
            result = await trial.place_bet(
                market="moneyline",
                selection=selection,
                amount=self.bet_amount,
                reference_sequence=reference_sequence,
            )

            self.state.bets_placed += 1
            self.state.total_wagered += self.bet_amount

            logger.info(
                "Bet #%d: %.2f on %s (prob=%.2f)",
                self.state.bets_placed,
                result.amount,
                result.selection,
                result.probability,
            )

        except StaleReferenceError:
            logger.debug("Bet skipped: stale reference")
        except InsufficientBalanceError:
            logger.warning("Bet skipped: insufficient balance")
        except BettingClosedError:
            logger.debug("Bet skipped: betting closed")
        except Exception as e:
            logger.error("Bet failed: %s", e)

    def stop(self):
        """Stop the agent gracefully."""
        logger.info("Stopping agent...")
        self._running = False

    def _print_summary(self):
        """Print agent session summary."""
        runtime = datetime.now() - self.state.start_time
        logger.info("=" * 50)
        logger.info("Agent Session Summary")
        logger.info("=" * 50)
        logger.info("Runtime: %s", runtime)
        logger.info("Bets placed: %d", self.state.bets_placed)
        logger.info("Total wagered: %.2f", self.state.total_wagered)
        logger.info("Reconnections: %d", self.state.reconnect_count)
        logger.info("Last sequence: %d", self.state.last_sequence)
        logger.info("=" * 50)


async def main():
    parser = argparse.ArgumentParser(
        description="Robust external betting agent",
        epilog="""
Examples:
  Standalone mode:
    python robust_agent.py --gateway http://localhost:8080 --agent-id my-agent

  Dashboard mode (explicit):
    python robust_agent.py --dashboard http://localhost:8000 --trial-id nba-game-xxx --agent-id my-agent

  Dashboard mode (from config/env):
    # Set DOJOZERO_DASHBOARD_URLS=http://dash1:8000,http://dash2:8000
    # Or configure ~/.dojozero/config.yaml with dashboard_urls
    python robust_agent.py --trial-id nba-game-xxx --agent-id my-agent
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Connection mode
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--gateway",
        help="Gateway URL for standalone mode (e.g., http://localhost:8080)",
    )
    mode_group.add_argument(
        "--dashboard",
        action="append",
        dest="dashboards",
        metavar="URL",
        help="Dashboard URL(s) for sharded mode. Can be specified multiple times.",
    )

    parser.add_argument(
        "--trial-id",
        help="Trial ID (required for dashboard mode)",
    )
    parser.add_argument(
        "--agent-id",
        default="robust-agent",
        help="Agent ID (default: robust-agent)",
    )
    parser.add_argument(
        "--bet-amount",
        type=float,
        default=10.0,
        help="Bet amount (default: 10.0)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.55,
        help="Probability threshold for betting (default: 0.55)",
    )
    parser.add_argument(
        "--max-reconnects",
        type=int,
        default=10,
        help="Max reconnection attempts (default: 10)",
    )
    parser.add_argument(
        "--api-key",
        help="API key for authentication (from 'dojo0 agents add')",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Resolve gateway URL
    gateway_url = args.gateway
    if args.dashboards:
        # Explicit --dashboard flag(s)
        if not args.trial_id:
            parser.error("--trial-id is required when using --dashboard")
        gateway_url = await discover_gateway(args.dashboards, args.trial_id)
        if not gateway_url:
            logger.error("Failed to discover gateway for trial %s", args.trial_id)
            return
    elif not gateway_url and args.trial_id:
        # No --gateway or --dashboard, but --trial-id provided
        # Use DojoClient's default config (env vars / config file)
        gateway_url = await discover_gateway(None, args.trial_id)
        if not gateway_url:
            logger.error("Failed to discover gateway for trial %s", args.trial_id)
            return

    if not gateway_url:
        parser.error(
            "Either --gateway or (--dashboard/config + --trial-id) is required"
        )

    agent = RobustBettingAgent(
        gateway_url=gateway_url,
        agent_id=args.agent_id,
        bet_amount=args.bet_amount,
        bet_threshold=args.threshold,
        max_reconnect_attempts=args.max_reconnects,
        api_key=args.api_key,
    )

    # Handle Ctrl+C gracefully by cancelling the task
    main_task = asyncio.current_task()

    def signal_handler():
        agent.stop()
        if main_task:
            main_task.cancel()

    try:
        import signal

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)
    except (NotImplementedError, AttributeError):
        pass  # Windows doesn't support add_signal_handler

    try:
        await agent.run()
    except asyncio.CancelledError:
        logger.info("Agent cancelled")
    except KeyboardInterrupt:
        agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
