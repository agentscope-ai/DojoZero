#!/usr/bin/env python3
"""Simple external agent example using the dojozero-client SDK.

This example demonstrates:
- Connecting to a trial (standalone or dashboard mode)
- Subscribing to events via SSE
- Placing bets based on events
- Querying balance and odds

Usage:
    # Standalone mode (single trial):
    dojo0 run --params your_trial.yaml --enable-gateway --gateway-port 8080
    python simple_agent.py --gateway http://localhost:8080 --agent-id my-agent

    # Dashboard mode (multiple trials):
    dojo0 serve --enable-gateway
    python simple_agent.py --dashboard http://localhost:8000 --agent-id my-agent
"""

import argparse
import asyncio
import logging

from dojozero_client import (
    DojoClient,
    EventEnvelope,
    StaleReferenceError,
    InsufficientBalanceError,
    BettingClosedError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class SimpleBettingAgent:
    """A simple agent that places bets based on events."""

    def __init__(self, bet_amount: float = 10.0, bet_threshold: float = 0.6):
        """Initialize the agent.

        Args:
            bet_amount: Amount to bet each time
            bet_threshold: Minimum probability to trigger a bet
        """
        self.bet_amount = bet_amount
        self.bet_threshold = bet_threshold
        self.bets_placed = 0

    def should_bet(
        self, event: EventEnvelope, home_prob: float | None
    ) -> tuple[bool, str]:
        """Decide whether to place a bet based on event and odds.

        Args:
            event: The event that triggered this decision
            home_prob: Current home team win probability (may be None if not set)

        Returns:
            Tuple of (should_bet, selection)
        """
        # Skip if odds not available
        if home_prob is None:
            return False, ""

        # Simple strategy: bet on the team with higher probability
        # Only bet if probability exceeds threshold
        if home_prob >= self.bet_threshold:
            return True, "home"
        elif (1 - home_prob) >= self.bet_threshold:
            return True, "away"
        return False, ""

    async def run(
        self,
        agent_id: str,
        trial_id: str,
        gateway_url: str | None = None,
        dashboard_urls: list[str] | None = None,
        api_key: str | None = None,
    ):
        """Run the agent.

        Args:
            agent_id: Unique agent identifier
            trial_id: Trial ID (required)
            gateway_url: Gateway URL for standalone mode (e.g., "http://localhost:8080")
            dashboard_urls: Dashboard URLs for sharded mode (e.g., ["http://localhost:8000"])
            api_key: API key for authentication (from dojo0 agents add)
        """
        # Create client with config
        client = DojoClient(
            gateway_url=gateway_url,
            dashboard_urls=dashboard_urls,
        )

        # Discovery mode: find the specified trial
        if dashboard_urls or (not gateway_url):
            logger.info("Discovering trial '%s'...", trial_id)
            gateways = await client.discover_trials()

            if not gateways:
                logger.error("No trials available")
                return

            matching = [g for g in gateways if g.trial_id == trial_id]
            if not matching:
                logger.error(
                    "Trial '%s' not found. Available: %s",
                    trial_id,
                    [g.trial_id for g in gateways],
                )
                return
            selected = matching[0]

            # Use full URL from discovery
            gateway_url = selected.url

        if not gateway_url:
            raise ValueError("No gateway URL available")

        logger.info("Connecting to %s as agent '%s'", gateway_url, agent_id)

        async with client.connect_trial(
            gateway_url=gateway_url,
            agent_id=agent_id,
            initial_balance=1000.0,
            api_key=api_key,
        ) as trial:
            # Get trial metadata
            metadata = await trial.get_trial_metadata()
            logger.info(
                "Connected to trial '%s': %s vs %s",
                metadata.trial_id,
                metadata.away_team,
                metadata.home_team,
            )

            # Get initial balance
            balance = await trial.get_balance()
            logger.info("Starting balance: %s", balance.balance)

            # Subscribe to events via SSE streaming
            logger.info("Subscribing to events (SSE streaming)...")
            event_count = 0
            async for event in trial.events():
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
                await self.handle_event(trial, event)

    async def handle_event(self, trial, event: EventEnvelope):
        """Handle a single event.

        Args:
            trial: The trial connection
            event: The event to handle
        """
        event_type = event.payload.get("event_type", "unknown")
        logger.debug("Event [seq=%d]: %s", event.sequence, event_type)

        # Skip non-game events
        if not event_type.startswith("event."):
            return

        # Get current odds
        try:
            odds = await trial.get_current_odds()
        except Exception as e:
            logger.warning("Failed to get odds: %s", e)
            return

        if not odds.betting_open:
            logger.debug("Betting is closed")
            return

        # Decide whether to bet
        should_bet, selection = self.should_bet(event, odds.home_probability)
        if not should_bet:
            return

        # Place the bet
        try:
            result = await trial.place_bet(
                market="moneyline",
                selection=selection,
                amount=self.bet_amount,
                reference_sequence=event.sequence,
            )
            self.bets_placed += 1
            logger.info(
                "Bet #%d placed: %s %.2f on %s (prob=%.2f, bet_id=%s)",
                self.bets_placed,
                result.market,
                result.amount,
                result.selection,
                result.probability,
                result.bet_id,
            )

            # Log updated balance
            balance = await trial.get_balance()
            logger.info("Current balance: %s", balance.balance)

        except StaleReferenceError:
            logger.warning("Bet rejected: stale reference sequence")
        except InsufficientBalanceError:
            logger.warning("Bet rejected: insufficient balance")
        except BettingClosedError:
            logger.warning("Bet rejected: betting closed")
        except Exception as e:
            logger.error("Bet failed: %s", e)


async def main():
    parser = argparse.ArgumentParser(
        description="Simple external betting agent",
        epilog="""
Examples:
  Standalone mode:
    python simple_agent.py --gateway http://localhost:8080 --trial-id my-trial --agent-id my-agent

  Dashboard mode:
    python simple_agent.py --dashboard http://localhost:8000 --trial-id nba-game-401810734-xxx --agent-id my-agent
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Connection mode (mutually exclusive, optional if env/config set)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--gateway",
        help="Gateway URL for standalone mode (e.g., http://localhost:8080)",
    )
    mode_group.add_argument(
        "--dashboard",
        help="Dashboard URL for sharded mode (e.g., http://localhost:8000)",
    )

    parser.add_argument(
        "--trial-id",
        required=True,
        help="Trial ID (required)",
    )
    parser.add_argument(
        "--agent-id",
        default="simple-agent",
        help="Agent ID (default: simple-agent)",
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
        default=0.6,
        help="Probability threshold for betting (default: 0.6)",
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

    # Note: --gateway/--dashboard not required if DOJOZERO_GATEWAY_URL env var
    # or ~/.dojozero/config.yaml is configured

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    agent = SimpleBettingAgent(
        bet_amount=args.bet_amount,
        bet_threshold=args.threshold,
    )

    try:
        await agent.run(
            agent_id=args.agent_id,
            trial_id=args.trial_id,
            gateway_url=args.gateway,
            dashboard_urls=[args.dashboard] if args.dashboard else None,
            api_key=args.api_key,
        )
    except KeyboardInterrupt:
        logger.info("Agent stopped by user")
    except Exception as e:
        logger.error("Agent error: %s", e)
        raise


if __name__ == "__main__":
    asyncio.run(main())
