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

from dojozero_client._client import AgentResult, DojoClient, EventEnvelope
from dojozero_client._config import (
    CONFIG_DIR,
    PID_FILE,
    SOCKET_PATH,
    TRIALS_DIR,
    load_config,
)
from dojozero_client._credentials import load_api_key
from dojozero_client._rpc import RPCError, RPCServer

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


def _trial_state_dir(trial_id: str) -> Path:
    """Get state directory for a specific trial."""
    return CONFIG_DIR / "trials" / trial_id


def _default_notify() -> list[str]:
    return ["file"]


def _default_filters() -> list[str]:
    return ["event.*", "odds.*"]


def _default_strategy_config() -> dict[str, Any]:
    return {}


def _unset_state_dir() -> Path:
    """Sentinel factory - state_dir will be set in __post_init__."""
    return Path()  # Placeholder, will be replaced


@dataclass
class DaemonConfig:
    """Configuration for the daemon.

    State is stored in ~/.dojozero/trials/{trial_id}/ to support
    multiple concurrent trials.
    """

    trial_id: str
    api_key: str = ""
    state_dir: Path = field(default_factory=_unset_state_dir)
    strategy: str | None = None
    strategy_config: dict[str, Any] = field(default_factory=_default_strategy_config)
    auto_bet: bool = False
    notify: list[str] = field(default_factory=_default_notify)
    filters: list[str] = field(default_factory=_default_filters)

    def __post_init__(self) -> None:
        # Auto-compute state_dir from trial_id if not explicitly set
        if self.state_dir == Path():
            self.state_dir = _trial_state_dir(self.trial_id)


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
    session_key: str = ""  # Session key for secure reconnection/unregistration
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
            "session_key": self.session_key,
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
            session_key=data.get("session_key", ""),
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
        config = DaemonConfig(trial_id="lal-bos-2026-02-23")
        daemon = Daemon(config)
        await daemon.start()
    """

    def __init__(self, config: DaemonConfig):
        """Initialize daemon.

        Args:
            config: Daemon configuration
        """
        self.config = config
        self.client = DojoClient()
        self.state_dir = config.state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.running = False
        self.strategy: Strategy | None = None
        self._state = DaemonState()
        self._stop_event: asyncio.Event | None = None
        self._seen_uids: set[str] = set()
        self._needs_balance_refresh = False

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
        gateway_url = load_config().get_gateway_url(self.config.trial_id)
        logger.info(
            "Starting daemon for trial %s at %s",
            self.config.trial_id,
            gateway_url,
        )

        # Check for existing state to resume from
        resume_sequence = 0
        stored_session_key = ""
        existing_state = self._read_state()
        if existing_state:
            resume_sequence = existing_state.get("last_event_sequence", 0)
            stored_session_key = existing_state.get("session_key", "")
            if resume_sequence > 0:
                logger.info(
                    "Resuming from sequence %d (from previous session)", resume_sequence
                )

        # Seed seen UIDs from existing events.jsonl to prevent duplicates on reconnect
        self._seen_uids = self._load_seen_uids()

        try:
            async with (
                self.client.connect_trial(
                    gateway_url=gateway_url,
                    api_key=self.config.api_key,
                    initial_balance=1000.0,  # Default balance for new agents
                    session_key=stored_session_key,  # Use stored session key for reconnection
                ) as trial
            ):
                # Set resume sequence for event replay
                if resume_sequence > 0:
                    trial.set_resume_sequence(resume_sequence)

                # Initialize state
                balance = await trial.get_balance()
                self._state = DaemonState(
                    trial_id=self.config.trial_id,
                    agent_id=trial.agent_id,
                    session_key=trial.session_key,  # Store session key for reconnection
                    status="connected",
                    balance=balance.balance,
                    holdings=[
                        {
                            "event_id": h.event_id,
                            "selection": h.selection,
                            "bet_type": h.bet_type,
                            "shares": h.shares,
                        }
                        for h in balance.holdings
                    ],
                    last_event_sequence=resume_sequence,  # Preserve sequence
                )
                self._save_state()
                logger.info("Connected as agent %s", trial.agent_id)

                # Main event loop
                await self._event_loop(trial)

                # Check if trial ended naturally
                if trial.trial_ended is not None:
                    ended = trial.trial_ended
                    logger.info(
                        "Trial ended (reason=%s, agents=%d)",
                        ended.reason,
                        len(ended.final_results),
                    )
                    self._state.status = ended.reason
                    if ended.final_results:
                        self._write_results(ended.reason, ended.final_results)
                    self._save_state()

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
        async for event in trial.events(
            event_types=self.config.filters,
            raise_on_trial_end=False,
        ):
            if not self.running:
                break

            await self._handle_event(trial, event)

    async def _handle_event(
        self, trial: "TrialConnection", event: EventEnvelope
    ) -> None:
        """Process an incoming event."""
        # Skip events already seen (replayed on reconnect)
        uid = event.payload.get("uid", "")
        if uid and uid in self._seen_uids:
            return
        if uid:
            self._seen_uids.add(uid)

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

        # Deferred balance refresh after a failed post-bet refresh
        if self._needs_balance_refresh:
            try:
                balance = await trial.get_balance()
                self._state.balance = balance.balance
                self._state.holdings = [
                    {
                        "bet_type": h.bet_type,
                        "selection": h.selection,
                        "shares": h.shares,
                    }
                    for h in balance.holdings
                ]
                self._needs_balance_refresh = False
                self._save_state()
                logger.info("Deferred balance refresh succeeded")
            except Exception:
                pass  # Will retry on next event

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
                    # Refresh balance after bet
                    try:
                        balance = await trial.get_balance()
                        self._state.balance = balance.balance
                        self._state.holdings = [
                            {
                                "bet_type": h.bet_type,
                                "selection": h.selection,
                                "shares": h.shares,
                            }
                            for h in balance.holdings
                        ]
                        self._needs_balance_refresh = False
                    except Exception as e:
                        logger.warning(
                            "Failed to refresh balance after auto-bet, applying optimistic update: %s",
                            e,
                        )
                        self._state.balance = max(
                            0.0, self._state.balance - decision["amount"]
                        )
                        existing = next(
                            (
                                h
                                for h in self._state.holdings
                                if h.get("bet_type") == decision["market"]
                                and h.get("selection") == decision["selection"]
                            ),
                            None,
                        )
                        if existing:
                            existing["shares"] = (
                                existing.get("shares", 0) + decision["amount"]
                            )
                        else:
                            self._state.holdings.append(
                                {
                                    "bet_type": decision["market"],
                                    "selection": decision["selection"],
                                    "shares": decision["amount"],
                                }
                            )
                        self._needs_balance_refresh = True
                    self._save_state()
            except Exception as e:
                logger.warning("Strategy decision error: %s", e)

    def _update_state_from_event(self, event: dict[str, Any]) -> None:
        """Update daemon state from an event."""
        payload = event.get("payload", {})
        # Use the inner event_type (e.g., "event.nba_game_update") not the
        # outer SSE type (always "event").
        event_type = payload.get("event_type", "") or event.get("type", "")
        sequence = event.get("sequence", 0)

        if sequence > self._state.last_event_sequence:
            self._state.last_event_sequence = sequence

        # Update game state from game events
        if "game" in event_type.lower() or "play" in event_type.lower():
            # Extract game state fields (handle both camelCase and snake_case)
            self._state.game_state.update(
                {
                    k: v
                    for k, v in payload.items()
                    if k
                    in (
                        "period",
                        "clock",
                        "game_clock",
                        "quarter",
                        "time",
                        "home_score",
                        "away_score",
                        "homeScore",
                        "awayScore",
                    )
                }
            )
            # Normalize key names to snake_case
            if "homeScore" in self._state.game_state:
                self._state.game_state["home_score"] = self._state.game_state.pop(
                    "homeScore"
                )
            if "awayScore" in self._state.game_state:
                self._state.game_state["away_score"] = self._state.game_state.pop(
                    "awayScore"
                )
            if "game_clock" in self._state.game_state:
                self._state.game_state["clock"] = self._state.game_state.pop(
                    "game_clock"
                )

        # Update odds from odds events
        if "odds" in event_type.lower():
            # Odds may be nested under payload.odds.moneyline or flat on payload
            odds_data = payload.get("odds", {})
            moneyline = odds_data.get("moneyline", {})
            # Try nested path first, fall back to flat payload
            self._state.current_odds = {
                "home_probability": moneyline.get(
                    "home_probability",
                    payload.get("homeProbability", payload.get("home_probability", 0)),
                ),
                "away_probability": moneyline.get(
                    "away_probability",
                    payload.get("awayProbability", payload.get("away_probability", 0)),
                ),
            }

        # Update balance from balance events
        if "balance" in event_type.lower():
            self._state.balance = payload.get("balance", self._state.balance)

        self._save_state()

    def _check_notification(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Determine if event warrants user notification."""
        payload = event.get("payload", {})
        event_type = payload.get("event_type", "") or event.get("type", "")

        # Game updates (scores, quarter changes)
        if any(k in event_type.lower() for k in ("game", "play", "score")):
            home = payload.get("homeScore", payload.get("home_score", "?"))
            away = payload.get("awayScore", payload.get("away_score", "?"))
            period = payload.get("period", payload.get("quarter", ""))
            clock = payload.get(
                "game_clock", payload.get("clock", payload.get("time", ""))
            )
            return {
                "type": "game_update",
                "message": f"Score: {away}-{home} (Q{period} {clock})",
            }

        # Significant odds shifts (>5%)
        if "odds" in event_type.lower():
            prev = self._state.current_odds.get("home_probability", 0)
            odds_data = payload.get("odds", {})
            moneyline = odds_data.get("moneyline", {})
            curr = moneyline.get(
                "home_probability",
                payload.get("homeProbability", payload.get("home_probability", 0)),
            )
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

    def _load_seen_uids(self) -> set[str]:
        """Load UIDs from existing events.jsonl for dedup on reconnect."""
        uids: set[str] = set()
        events_file = self.state_dir / "events.jsonl"
        if events_file.exists():
            for line in events_file.read_text().strip().split("\n"):
                if line:
                    try:
                        uid = json.loads(line).get("payload", {}).get("uid", "")
                        if uid:
                            uids.add(uid)
                    except json.JSONDecodeError:
                        continue
        return uids

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

    def _write_results(self, reason: str, results: list[AgentResult]) -> None:
        """Write final trial results to results.json."""
        results_file = self.state_dir / "results.json"
        data = {
            "trial_id": self._state.trial_id,
            "status": reason,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "results": [
                {
                    "agent_id": r.agent_id,
                    "final_balance": r.final_balance,
                    "net_profit": r.net_profit,
                    "total_bets": r.total_bets,
                    "win_rate": r.win_rate,
                    "roi": r.roi,
                }
                for r in results
            ],
        }
        with open(results_file, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Results written to %s", results_file)

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


def get_daemon_status(
    trial_id: str | None = None, state_dir: Path | None = None
) -> dict[str, Any] | None:
    """Get current daemon status.

    Args:
        trial_id: Trial ID to check status for
        state_dir: Override state directory (defaults to ~/.dojozero/trials/{trial_id}/)

    Returns:
        State dict or None if no daemon is running
    """
    if state_dir is None:
        if trial_id:
            state_dir = _trial_state_dir(trial_id)
        else:
            state_dir = CONFIG_DIR

    state_file = state_dir / "state.json"
    if not state_file.exists():
        return None

    try:
        return json.loads(state_file.read_text())
    except Exception:
        return None


def is_daemon_running(
    trial_id: str | None = None, state_dir: Path | None = None
) -> bool:
    """Check if a daemon is currently running.

    Args:
        trial_id: Trial ID to check
        state_dir: Override state directory (defaults to ~/.dojozero/trials/{trial_id}/)

    Returns:
        True if daemon is running
    """
    if state_dir is None:
        if trial_id:
            state_dir = _trial_state_dir(trial_id)
        else:
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


def stop_daemon(trial_id: str | None = None, state_dir: Path | None = None) -> bool:
    """Stop the running daemon.

    Args:
        trial_id: Trial ID to stop
        state_dir: Override state directory (defaults to ~/.dojozero/trials/{trial_id}/)

    Returns:
        True if daemon was stopped
    """
    if state_dir is None:
        if trial_id:
            state_dir = _trial_state_dir(trial_id)
        else:
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


def list_running_trials() -> list[str]:
    """List all trials with running daemons.

    Returns:
        List of trial IDs with active daemons
    """
    trials_dir = CONFIG_DIR / "trials"
    if not trials_dir.exists():
        return []

    running = []
    for trial_dir in trials_dir.iterdir():
        if trial_dir.is_dir():
            trial_id = trial_dir.name
            if is_daemon_running(trial_id=trial_id):
                running.append(trial_id)

    return running


# =============================================================================
# Unified Daemon (New Architecture)
# =============================================================================


class TrialHandler:
    """Handler for a single trial connection within UnifiedDaemon.

    Manages the SSE connection, event processing, and state persistence
    for one trial.
    """

    def __init__(
        self,
        trial_id: str,
        api_key: str,
        client: DojoClient,
        filters: list[str] | None = None,
    ):
        """Initialize trial handler.

        Args:
            trial_id: Trial identifier
            api_key: API key for authentication
            client: Shared DojoClient instance
            filters: Event type filters
        """
        self.trial_id = trial_id
        self.api_key = api_key
        self.client = client
        self.filters = filters or ["event.*", "odds.*"]

        self.state_dir = TRIALS_DIR / trial_id
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self._state = DaemonState(trial_id=trial_id)
        self._trial: "TrialConnection | None" = None
        self._context_manager: Any = None  # The async context manager
        self._event_task: asyncio.Task[None] | None = None
        self._running = False
        self._seen_uids: set[str] = set()
        self._needs_balance_refresh = False

    @property
    def agent_id(self) -> str:
        """Get the agent ID for this trial."""
        return self._state.agent_id

    @property
    def is_connected(self) -> bool:
        """Check if connected to trial."""
        return self._trial is not None and self._running

    async def connect(self) -> None:
        """Connect to the trial and start event streaming."""
        if self._running:
            return

        # Seed seen UIDs from existing events.jsonl to prevent duplicates on reconnect
        self._seen_uids = self._load_seen_uids()

        # Check for existing state to resume from
        resume_sequence = 0
        existing_state = self._read_state()
        if existing_state:
            resume_sequence = existing_state.get("last_event_sequence", 0)
            if resume_sequence > 0:
                logger.info(
                    "Trial %s: Resuming from sequence %d",
                    self.trial_id,
                    resume_sequence,
                )

        # Check for existing session key from previous state
        stored_session_key = (
            existing_state.get("session_key", "") if existing_state else ""
        )

        # Connect to trial using async context manager
        gateway_url = load_config().get_gateway_url(self.trial_id)
        self._context_manager = self.client.connect_trial(
            gateway_url=gateway_url,
            api_key=self.api_key,
            initial_balance=1000.0,
            session_key=stored_session_key,
        )
        # Enter the context manager manually
        self._trial = await self._context_manager.__aenter__()

        trial = self._trial  # Local reference for type checker
        assert trial is not None

        if resume_sequence > 0:
            trial.set_resume_sequence(resume_sequence)

        # Initialize state (preserve session key from connection)
        balance = await trial.get_balance()
        self._state = DaemonState(
            trial_id=self.trial_id,
            agent_id=trial.agent_id,
            session_key=trial.session_key,  # Store session key for reconnection
            status="connected",
            balance=balance.balance,
            holdings=[
                {
                    "event_id": h.event_id,
                    "selection": h.selection,
                    "bet_type": h.bet_type,
                    "shares": h.shares,
                }
                for h in balance.holdings
            ],
            last_event_sequence=resume_sequence,
        )
        self._save_state()
        logger.info("Trial %s: Connected as agent %s", self.trial_id, trial.agent_id)

        # Start event streaming task
        self._running = True
        self._event_task = asyncio.create_task(self._event_loop())

    async def disconnect(self) -> None:
        """Disconnect from the trial."""
        self._running = False

        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None

        # Exit the context manager
        if self._context_manager:
            try:
                await self._context_manager.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("Error closing trial connection: %s", e)
            self._context_manager = None
            self._trial = None

        self._state.status = "disconnected"
        self._save_state()
        logger.info("Trial %s: Disconnected", self.trial_id)

    async def place_bet(
        self, amount: float, market: str, selection: str
    ) -> dict[str, Any]:
        """Place a bet on this trial.

        Args:
            amount: Bet amount
            market: Market type (moneyline, spread, total)
            selection: Selection (home, away, over, under)

        Returns:
            Bet result dict with bet_id, status, etc.
        """
        if not self._trial:
            raise RPCError("NOT_CONNECTED", f"Not connected to trial {self.trial_id}")

        result = await self._trial.place_bet(
            market=market,
            selection=selection,
            amount=amount,
            reference_sequence=self._state.last_event_sequence,
        )

        # Log bet
        bet_record = {
            "bet_id": result.bet_id,
            "market": result.market,
            "selection": result.selection,
            "amount": result.amount,
            "probability": result.probability,
            "status": result.status,
            "placed_at": result.placed_at.isoformat(),
        }
        self._append_bet(bet_record)

        # Refresh balance and holdings from server
        await self._refresh_balance_after_bet(
            result.amount, result.market, result.selection
        )

        return bet_record

    async def _refresh_balance_after_bet(
        self, amount: float, market: str, selection: str
    ) -> None:
        """Refresh balance from server after a bet, with optimistic fallback."""
        try:
            if self._trial:
                balance = await self._trial.get_balance()
                self._state.balance = balance.balance
                self._state.holdings = [
                    {
                        "bet_type": h.bet_type,
                        "selection": h.selection,
                        "shares": h.shares,
                    }
                    for h in balance.holdings
                ]
                self._needs_balance_refresh = False
        except Exception as e:
            logger.warning(
                "Trial %s: Failed to refresh balance after bet, applying optimistic update: %s",
                self.trial_id,
                e,
            )
            # Optimistic: deduct amount and add holding
            self._state.balance = max(0.0, self._state.balance - amount)
            # Merge into existing holdings
            existing = next(
                (
                    h
                    for h in self._state.holdings
                    if h.get("bet_type") == market and h.get("selection") == selection
                ),
                None,
            )
            if existing:
                existing["shares"] = existing.get("shares", 0) + amount
            else:
                self._state.holdings.append(
                    {"bet_type": market, "selection": selection, "shares": amount}
                )
            self._needs_balance_refresh = True
        self._save_state()

    async def get_balance(self) -> dict[str, Any]:
        """Get current balance."""
        if not self._trial:
            raise RPCError("NOT_CONNECTED", f"Not connected to trial {self.trial_id}")

        balance = await self._trial.get_balance()
        return {
            "balance": balance.balance,
            "holdings": [
                {
                    "event_id": h.event_id,
                    "selection": h.selection,
                    "bet_type": h.bet_type,
                    "shares": h.shares,
                }
                for h in balance.holdings
            ],
        }

    async def get_status(self) -> dict[str, Any]:
        """Get current trial status with fresh balance from server."""
        # Refresh balance from server if connected
        if self._trial:
            try:
                balance = await self._trial.get_balance()
                self._state.balance = balance.balance
                self._state.holdings = [
                    {
                        "event_id": h.event_id,
                        "selection": h.selection,
                        "bet_type": h.bet_type,
                        "shares": h.shares,
                    }
                    for h in balance.holdings
                ]
                self._save_state()
            except Exception as e:
                logger.warning("Failed to refresh balance: %s", e)
        return self._state.to_dict()

    def get_events(self, count: int = 20) -> list[dict[str, Any]]:
        """Get recent events."""
        events_file = self.state_dir / "events.jsonl"
        if not events_file.exists():
            return []

        lines = events_file.read_text().strip().split("\n")
        events = []
        for line in lines[-count:]:
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events

    async def _event_loop(self) -> None:
        """Main event processing loop."""
        if not self._trial:
            return

        try:
            async for event in self._trial.events(
                event_types=self.filters,
                raise_on_trial_end=False,
            ):
                if not self._running:
                    break
                await self._handle_event(event)

            # Check if trial ended naturally
            if self._trial.trial_ended is not None:
                ended = self._trial.trial_ended
                logger.info(
                    "Trial %s ended (reason=%s, agents=%d)",
                    self.trial_id,
                    ended.reason,
                    len(ended.final_results),
                )
                self._state.status = ended.reason
                if ended.final_results:
                    self._write_results(ended.reason, ended.final_results)
                self._save_state()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("Trial %s: Event loop error: %s", self.trial_id, e)
            self._state.status = "error"
            self._save_state()

    async def _handle_event(self, event: EventEnvelope) -> None:
        """Process an incoming event."""
        # Skip events already seen (replayed on reconnect)
        uid = event.payload.get("uid", "")
        if uid and uid in self._seen_uids:
            return
        if uid:
            self._seen_uids.add(uid)

        event_dict = {
            "type": event.event_type or event.payload.get("event_type", ""),
            "payload": event.payload,
            "sequence": event.sequence,
            "timestamp": event.timestamp.isoformat(),
        }

        # Log event
        self._append_event(event_dict)

        # Update state
        self._update_state_from_event(event_dict)

        # Deferred balance refresh after a failed post-bet refresh
        if self._needs_balance_refresh and self._trial:
            try:
                balance = await self._trial.get_balance()
                self._state.balance = balance.balance
                self._state.holdings = [
                    {
                        "bet_type": h.bet_type,
                        "selection": h.selection,
                        "shares": h.shares,
                    }
                    for h in balance.holdings
                ]
                self._needs_balance_refresh = False
                self._save_state()
                logger.info(
                    "Trial %s: Deferred balance refresh succeeded", self.trial_id
                )
            except Exception:
                pass  # Will retry on next event

    def _update_state_from_event(self, event: dict[str, Any]) -> None:
        """Update state from an event."""
        payload = event.get("payload", {})
        # Use the inner event_type (e.g., "event.odds_update") not the
        # outer SSE type (always "event").
        event_type = payload.get("event_type", "") or event.get("type", "")
        sequence = event.get("sequence", 0)

        if sequence > self._state.last_event_sequence:
            self._state.last_event_sequence = sequence

        # Update odds from odds events
        if "odds" in event_type.lower():
            odds_data = payload.get("odds", payload)
            moneyline = odds_data.get("moneyline", {})
            self._state.current_odds = {
                "home_probability": moneyline.get(
                    "home_probability", odds_data.get("home_probability", 0)
                ),
                "away_probability": moneyline.get(
                    "away_probability", odds_data.get("away_probability", 0)
                ),
            }

        self._save_state()

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

    def _load_seen_uids(self) -> set[str]:
        """Load UIDs from existing events.jsonl for dedup on reconnect."""
        uids: set[str] = set()
        events_file = self.state_dir / "events.jsonl"
        if events_file.exists():
            for line in events_file.read_text().strip().split("\n"):
                if line:
                    try:
                        uid = json.loads(line).get("payload", {}).get("uid", "")
                        if uid:
                            uids.add(uid)
                    except json.JSONDecodeError:
                        continue
        return uids

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

    def _write_results(self, reason: str, results: list[AgentResult]) -> None:
        """Write final trial results to results.json."""
        results_file = self.state_dir / "results.json"
        data = {
            "trial_id": self.trial_id,
            "status": reason,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "results": [
                {
                    "agent_id": r.agent_id,
                    "final_balance": r.final_balance,
                    "net_profit": r.net_profit,
                    "total_bets": r.total_bets,
                    "win_rate": r.win_rate,
                    "roi": r.roi,
                }
                for r in results
            ],
        }
        with open(results_file, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Trial %s: Results written to %s", self.trial_id, results_file)


class UnifiedDaemon:
    """Unified daemon managing multiple trial connections.

    Provides a single daemon process that:
    - Manages connections to multiple trials
    - Exposes Unix socket RPC for CLI commands
    - Handles all authentication internally

    Usage:
        daemon = UnifiedDaemon()
        await daemon.start()  # Runs until stopped
    """

    def __init__(self) -> None:
        """Initialize unified daemon."""
        self._trials: dict[str, TrialHandler] = {}
        self._rpc = RPCServer(SOCKET_PATH)
        self._api_key: str | None = None
        self._client = DojoClient()
        self._stop_event: asyncio.Event | None = None
        self._running = False

        # Register RPC handlers
        self._rpc.register("join", self._handle_join)
        self._rpc.register("leave", self._handle_leave)
        self._rpc.register("bet", self._handle_bet)
        self._rpc.register("status", self._handle_status)
        self._rpc.register("list", self._handle_list)
        self._rpc.register("events", self._handle_events)
        self._rpc.register("balance", self._handle_balance)
        self._rpc.register("ping", self._handle_ping)

    async def start(self) -> None:
        """Start the unified daemon."""
        # Load API key from credentials file
        self._api_key = load_api_key()
        if not self._api_key:
            raise RuntimeError(
                "No API key configured. Run 'dojozero-agent config --api-key <key>'"
            )

        self._write_pid()
        self._setup_signals()
        self._stop_event = asyncio.Event()
        self._running = True

        logger.info("Starting unified daemon")

        try:
            await self._rpc.start()
            logger.info("RPC server started at %s", SOCKET_PATH)

            # Keep running until stopped
            await self._stop_event.wait()

        except asyncio.CancelledError:
            logger.info("Daemon cancelled")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the daemon and all trial connections."""
        self._running = False

        # Disconnect all trials
        for trial_id in list(self._trials.keys()):
            try:
                await self._trials[trial_id].disconnect()
            except Exception as e:
                logger.warning("Error disconnecting trial %s: %s", trial_id, e)
        self._trials.clear()

        # Stop RPC server
        await self._rpc.stop()

        # Cleanup PID
        self._cleanup_pid()

        logger.info("Unified daemon stopped")

    # -------------------------------------------------------------------------
    # RPC Handlers
    # -------------------------------------------------------------------------

    async def _handle_join(
        self, trial_id: str, filters: list[str] | None = None
    ) -> dict[str, Any]:
        """Join a trial."""
        if trial_id in self._trials:
            handler = self._trials[trial_id]
            return {
                "status": "already_joined",
                "agent_id": handler.agent_id,
            }

        if not self._api_key:
            raise RPCError("NO_API_KEY", "No API key configured")

        handler = TrialHandler(
            trial_id=trial_id,
            api_key=self._api_key,
            client=self._client,
            filters=filters,
        )

        try:
            await handler.connect()
        except Exception as e:
            raise RPCError("CONNECTION_FAILED", str(e)) from e

        self._trials[trial_id] = handler
        return {
            "status": "joined",
            "agent_id": handler.agent_id,
        }

    async def _handle_leave(self, trial_id: str) -> dict[str, Any]:
        """Leave a trial."""
        if trial_id not in self._trials:
            raise RPCError("NOT_FOUND", f"Not connected to trial {trial_id}")

        handler = self._trials.pop(trial_id)
        await handler.disconnect()
        return {"status": "left"}

    async def _handle_bet(
        self, trial_id: str, amount: float, market: str, selection: str
    ) -> dict[str, Any]:
        """Place a bet."""
        handler = self._get_handler(trial_id)
        return await handler.place_bet(amount, market, selection)

    async def _handle_status(self, trial_id: str | None = None) -> dict[str, Any]:
        """Get trial status."""
        handler = self._get_handler(trial_id)
        return await handler.get_status()

    async def _handle_list(self) -> dict[str, Any]:
        """List active trials with fresh balances."""
        trials = {}
        for trial_id, handler in self._trials.items():
            # get_status() refreshes balance from server
            status = await handler.get_status()
            trials[trial_id] = {
                "agent_id": handler.agent_id,
                "connected": handler.is_connected,
                "balance": status["balance"],
            }
        return {"trials": trials}

    async def _handle_events(
        self, trial_id: str | None = None, count: int = 20
    ) -> dict[str, Any]:
        """Get recent events."""
        handler = self._get_handler(trial_id)
        return {"events": handler.get_events(count)}

    async def _handle_balance(self, trial_id: str | None = None) -> dict[str, Any]:
        """Get balance."""
        handler = self._get_handler(trial_id)
        return await handler.get_balance()

    async def _handle_ping(self) -> dict[str, Any]:
        """Health check."""
        return {"status": "ok", "trials": len(self._trials)}

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _get_handler(self, trial_id: str | None) -> TrialHandler:
        """Get trial handler, auto-selecting if only one trial."""
        if trial_id:
            if trial_id not in self._trials:
                raise RPCError("NOT_FOUND", f"Not connected to trial {trial_id}")
            return self._trials[trial_id]

        if len(self._trials) == 0:
            raise RPCError("NO_TRIALS", "No trials connected")
        if len(self._trials) == 1:
            return next(iter(self._trials.values()))

        raise RPCError(
            "TRIAL_REQUIRED",
            f"Multiple trials connected ({len(self._trials)}), specify trial_id",
        )

    def _write_pid(self) -> None:
        """Write PID file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))

    def _cleanup_pid(self) -> None:
        """Remove PID file."""
        if PID_FILE.exists():
            PID_FILE.unlink()

    def _setup_signals(self) -> None:
        """Setup signal handlers for graceful shutdown."""

        def handle_signal(signum: int, _frame: Any) -> None:
            logger.info("Received signal %s, stopping...", signum)
            self._running = False
            if self._stop_event:
                self._stop_event.set()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)


def is_unified_daemon_running() -> bool:
    """Check if the unified daemon is running.

    Returns:
        True if unified daemon is running
    """
    if not PID_FILE.exists():
        return False

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


def stop_unified_daemon() -> bool:
    """Stop the unified daemon.

    Returns:
        True if daemon was stopped
    """
    if not PID_FILE.exists():
        return False

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink()
        return True
    except (ValueError, OSError):
        return False


__all__ = [
    # Legacy per-trial daemon (backward compatible)
    "Daemon",
    "DaemonConfig",
    "DaemonState",
    "Strategy",
    "get_daemon_status",
    "is_daemon_running",
    "stop_daemon",
    "list_running_trials",
    # Unified daemon (new)
    "UnifiedDaemon",
    "TrialHandler",
    "is_unified_daemon_running",
    "stop_unified_daemon",
]
