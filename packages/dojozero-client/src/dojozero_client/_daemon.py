"""Daemon mode for persistent trial connections.

Provides a long-running process that:
- Maintains SSE connection to a trial
- Persists state to ~/.dojozero/
- Supports strategy plugins for automated betting
- Writes notifications for external tools (e.g., OpenClaw)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from dojozero_client._client import DojoClient, EventEnvelope
from dojozero_client._config import CONFIG_DIR

if TYPE_CHECKING:
    from dojozero_client._client import TrialConnection

logger = logging.getLogger(__name__)


class Strategy(Protocol):
    """Protocol for betting strategy plugins.

    Strategies receive events and state, and return betting decisions.

    Example implementation:
        class MyStrategy:
            def __init__(self, config: dict[str, Any]):
                self.min_edge = config.get("min_edge", 0.10)

            def decide(self, event: dict, state: dict) -> dict | None:
                if "odds" not in event.get("type", ""):
                    return None
                odds = event.get("payload", {})
                if odds.get("home_probability", 0.5) > 0.6:
                    return {"market": "moneyline", "selection": "home", "amount": 50}
                return None
    """

    def decide(
        self, event: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Make a betting decision based on event and current state.

        Args:
            event: The incoming event dict with 'type' and 'payload'
            state: Current daemon state (balance, holdings, game_state, etc.)

        Returns:
            Betting decision dict with 'market', 'selection', 'amount' keys,
            or None to skip betting.
        """
        ...


def _default_state_dir() -> Path:
    return CONFIG_DIR


def _default_notify() -> list[str]:
    return ["file"]


def _default_filters() -> list[str]:
    return ["event.*", "odds.*"]


def _default_strategy_config() -> dict[str, Any]:
    return {}


@dataclass
class DaemonConfig:
    """Configuration for the daemon."""

    trial_id: str
    gateway_url: str = "http://localhost:8000"
    agent_id: str = ""
    api_key: str = ""
    state_dir: Path = field(default_factory=_default_state_dir)
    strategy: str | None = None
    strategy_config: dict[str, Any] = field(default_factory=_default_strategy_config)
    auto_bet: bool = False
    notify: list[str] = field(default_factory=_default_notify)
    filters: list[str] = field(default_factory=_default_filters)

    def __post_init__(self) -> None:
        if not self.agent_id:
            self.agent_id = f"agent-{os.getpid()}"


def _default_holdings() -> list[dict[str, Any]]:
    return []


def _default_game_state() -> dict[str, Any]:
    return {}


def _default_current_odds() -> dict[str, Any]:
    return {}


@dataclass
class DaemonState:
    """Serializable daemon state."""

    trial_id: str = ""
    agent_id: str = ""
    status: str = "disconnected"
    balance: float = 0.0
    holdings: list[dict[str, Any]] = field(default_factory=_default_holdings)
    last_event_sequence: int = 0
    last_updated: str = ""
    game_state: dict[str, Any] = field(default_factory=_default_game_state)
    current_odds: dict[str, Any] = field(default_factory=_default_current_odds)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "trial_id": self.trial_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "balance": self.balance,
            "holdings": self.holdings,
            "last_event_sequence": self.last_event_sequence,
            "last_updated": self.last_updated,
            "game_state": self.game_state,
            "current_odds": self.current_odds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DaemonState":
        """Create from dictionary."""
        return cls(
            trial_id=data.get("trial_id", ""),
            agent_id=data.get("agent_id", ""),
            status=data.get("status", "disconnected"),
            balance=data.get("balance", 0.0),
            holdings=data.get("holdings", []),
            last_event_sequence=data.get("last_event_sequence", 0),
            last_updated=data.get("last_updated", ""),
            game_state=data.get("game_state", {}),
            current_odds=data.get("current_odds", {}),
        )


class Daemon:
    """Daemon process for persistent trial connections.

    Maintains a long-running SSE connection to a trial, persists state,
    and optionally executes betting strategies.

    Usage:
        config = DaemonConfig(
            trial_id="lal-bos-2026-02-23",
            gateway_url="http://localhost:8000",
        )
        daemon = Daemon(config)
        await daemon.start()
    """

    def __init__(self, config: DaemonConfig):
        """Initialize daemon.

        Args:
            config: Daemon configuration
        """
        self.config = config
        self.client = DojoClient(gateway_url=config.gateway_url)
        self.state_dir = config.state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.running = False
        self.strategy: Strategy | None = None
        self._state = DaemonState()
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        """Start the daemon main loop."""
        self._write_pid()
        self._setup_signals()
        self._stop_event = asyncio.Event()

        if self.config.strategy:
            self.strategy = self._load_strategy(
                self.config.strategy, self.config.strategy_config
            )

        self.running = True
        logger.info(
            "Starting daemon for trial %s at %s",
            self.config.trial_id,
            self.config.gateway_url,
        )

        try:
            async with self.client.connect_trial(
                gateway_url=self.config.gateway_url,
                agent_id=self.config.agent_id,
            ) as trial:
                # Initialize state
                balance = await trial.get_balance()
                self._state = DaemonState(
                    trial_id=self.config.trial_id,
                    agent_id=trial.agent_id,
                    status="connected",
                    balance=balance.balance,
                    holdings=[
                        {"market": k, "shares": v} for k, v in balance.holdings.items()
                    ],
                )
                self._save_state()
                logger.info("Connected as agent %s", trial.agent_id)

                # Main event loop
                await self._event_loop(trial)

        except asyncio.CancelledError:
            logger.info("Daemon cancelled")
        except Exception as e:
            logger.exception("Daemon error: %s", e)
            self._state.status = "error"
            self._save_state()
            raise
        finally:
            self._state.status = "disconnected"
            self._save_state()
            self._cleanup_pid()

    async def stop(self) -> None:
        """Signal the daemon to stop."""
        self.running = False
        if self._stop_event:
            self._stop_event.set()

    async def _event_loop(self, trial: "TrialConnection") -> None:
        """Main event processing loop."""
        async for event in trial.events(event_types=self.config.filters):
            if not self.running:
                break

            await self._handle_event(trial, event)

    async def _handle_event(
        self, trial: "TrialConnection", event: EventEnvelope
    ) -> None:
        """Process an incoming event."""
        # Convert to dict for logging and strategy
        event_dict = {
            "type": event.event_type or event.payload.get("eventType", ""),
            "payload": event.payload,
            "sequence": event.sequence,
            "timestamp": event.timestamp.isoformat(),
        }

        # Log event
        self._append_event(event_dict)

        # Update state from event
        self._update_state_from_event(event_dict)

        # Check for notable events -> notify
        if notification := self._check_notification(event_dict):
            self._write_notification(notification)

        # Maybe make betting decision
        if self.config.auto_bet and self.strategy:
            try:
                decision = self.strategy.decide(event_dict, self._state.to_dict())
                if decision:
                    result = await trial.place_bet(
                        market=decision["market"],
                        selection=decision["selection"],
                        amount=decision["amount"],
                        reference_sequence=event.sequence,
                    )
                    self._append_bet(
                        {
                            "bet_id": result.bet_id,
                            "market": result.market,
                            "selection": result.selection,
                            "amount": result.amount,
                            "probability": result.probability,
                            "status": result.status,
                            "placed_at": result.placed_at.isoformat(),
                        }
                    )
                    self._write_notification(
                        {
                            "type": "bet_placed",
                            "message": f"Bet ${decision['amount']} on {decision['selection']} ({decision['market']})",
                        }
                    )
                    logger.info(
                        "Placed bet: %s on %s for $%s",
                        decision["market"],
                        decision["selection"],
                        decision["amount"],
                    )
            except Exception as e:
                logger.warning("Strategy decision error: %s", e)

    def _update_state_from_event(self, event: dict[str, Any]) -> None:
        """Update daemon state from an event."""
        event_type = event.get("type", "")
        payload = event.get("payload", {})
        sequence = event.get("sequence", 0)

        if sequence > self._state.last_event_sequence:
            self._state.last_event_sequence = sequence

        # Update game state from game events
        if "game" in event_type.lower() or "play" in event_type.lower():
            self._state.game_state.update(
                {
                    k: v
                    for k, v in payload.items()
                    if k
                    in ("period", "clock", "homeScore", "awayScore", "quarter", "time")
                }
            )
            # Normalize key names
            if "homeScore" in self._state.game_state:
                self._state.game_state["home_score"] = self._state.game_state.pop(
                    "homeScore"
                )
            if "awayScore" in self._state.game_state:
                self._state.game_state["away_score"] = self._state.game_state.pop(
                    "awayScore"
                )

        # Update odds from odds events
        if "odds" in event_type.lower():
            self._state.current_odds = {
                "home_probability": payload.get(
                    "homeProbability", payload.get("home_probability", 0)
                ),
                "away_probability": payload.get(
                    "awayProbability", payload.get("away_probability", 0)
                ),
            }

        # Update balance from balance events
        if "balance" in event_type.lower():
            self._state.balance = payload.get("balance", self._state.balance)

        self._save_state()

    def _check_notification(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Determine if event warrants user notification."""
        event_type = event.get("type", "")
        payload = event.get("payload", {})

        # Game updates (scores, quarter changes)
        if any(k in event_type.lower() for k in ("game", "play", "score")):
            home = payload.get("homeScore", payload.get("home_score", "?"))
            away = payload.get("awayScore", payload.get("away_score", "?"))
            period = payload.get("period", payload.get("quarter", ""))
            clock = payload.get("clock", payload.get("time", ""))
            return {
                "type": "game_update",
                "message": f"Score: {away}-{home} (Q{period} {clock})",
            }

        # Significant odds shifts (>5%)
        if "odds" in event_type.lower():
            prev = self._state.current_odds.get("home_probability", 0)
            curr = payload.get("homeProbability", payload.get("home_probability", 0))
            if prev and abs(curr - prev) > 0.05:
                return {
                    "type": "odds_shift",
                    "message": f"Odds shifted: {prev:.0%} -> {curr:.0%}",
                }

        # Bet settlements
        if "settle" in event_type.lower():
            return {
                "type": "bet_settled",
                "message": f"Bet settled: {payload.get('result', 'unknown')}",
            }

        return None

    def _load_strategy(
        self, module_path: str, config: dict[str, Any]
    ) -> Strategy | None:
        """Load a strategy plugin from a module path.

        Args:
            module_path: Dot-separated module path (e.g., "strategies.conservative")
            config: Configuration dict to pass to strategy

        Returns:
            Strategy instance or None if loading fails
        """
        import importlib

        try:
            module = importlib.import_module(module_path)
            strategy_cls = getattr(module, "Strategy")
            return strategy_cls(config)
        except Exception as e:
            logger.error("Failed to load strategy %s: %s", module_path, e)
            return None

    def _save_state(self) -> None:
        """Save current state to disk."""
        self._state.last_updated = datetime.now(timezone.utc).isoformat()
        state_file = self.state_dir / "state.json"
        state_file.write_text(json.dumps(self._state.to_dict(), indent=2))

    def _read_state(self) -> dict[str, Any]:
        """Read state from disk."""
        state_file = self.state_dir / "state.json"
        if state_file.exists():
            return json.loads(state_file.read_text())
        return {}

    def _append_event(self, event: dict[str, Any]) -> None:
        """Append event to event log."""
        events_file = self.state_dir / "events.jsonl"
        with open(events_file, "a") as f:
            f.write(json.dumps(event) + "\n")

    def _append_bet(self, bet: dict[str, Any]) -> None:
        """Append bet to bet history."""
        bets_file = self.state_dir / "bets.jsonl"
        with open(bets_file, "a") as f:
            f.write(json.dumps(bet) + "\n")

    def _write_notification(self, notif: dict[str, Any]) -> None:
        """Write notification for external consumers."""
        if "file" not in self.config.notify:
            return

        notif["ts"] = datetime.now(timezone.utc).isoformat()
        notif_file = self.state_dir / "notifications.jsonl"
        with open(notif_file, "a") as f:
            f.write(json.dumps(notif) + "\n")

    def _write_pid(self) -> None:
        """Write PID file for process management."""
        pid_file = self.state_dir / "daemon.pid"
        pid_file.write_text(str(os.getpid()))

    def _cleanup_pid(self) -> None:
        """Remove PID file."""
        pid_file = self.state_dir / "daemon.pid"
        if pid_file.exists():
            pid_file.unlink()

    def _setup_signals(self) -> None:
        """Setup signal handlers for graceful shutdown."""

        def handle_signal(signum: int, _frame: Any) -> None:
            logger.info("Received signal %s, stopping...", signum)
            self.running = False
            if self._stop_event:
                self._stop_event.set()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)


def get_daemon_status(state_dir: Path | None = None) -> dict[str, Any] | None:
    """Get current daemon status.

    Args:
        state_dir: State directory path, defaults to ~/.dojozero/

    Returns:
        State dict or None if no daemon is running
    """
    if state_dir is None:
        state_dir = CONFIG_DIR

    state_file = state_dir / "state.json"
    if not state_file.exists():
        return None

    try:
        return json.loads(state_file.read_text())
    except Exception:
        return None


def is_daemon_running(state_dir: Path | None = None) -> bool:
    """Check if a daemon is currently running.

    Args:
        state_dir: State directory path, defaults to ~/.dojozero/

    Returns:
        True if daemon is running
    """
    if state_dir is None:
        state_dir = CONFIG_DIR

    pid_file = state_dir / "daemon.pid"
    if not pid_file.exists():
        return False

    try:
        pid = int(pid_file.read_text().strip())
        # Check if process exists
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        # Process doesn't exist or invalid PID
        return False


def stop_daemon(state_dir: Path | None = None) -> bool:
    """Stop the running daemon.

    Args:
        state_dir: State directory path, defaults to ~/.dojozero/

    Returns:
        True if daemon was stopped
    """
    if state_dir is None:
        state_dir = CONFIG_DIR

    pid_file = state_dir / "daemon.pid"
    if not pid_file.exists():
        return False

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        pid_file.unlink()
        return True
    except (ValueError, OSError):
        return False


__all__ = [
    "Daemon",
    "DaemonConfig",
    "DaemonState",
    "Strategy",
    "get_daemon_status",
    "is_daemon_running",
    "stop_daemon",
]
