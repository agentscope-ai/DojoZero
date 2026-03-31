"""Daemon mode for persistent trial connections.

Provides a long-running process that:
- Maintains SSE connections to one or more trials
- Persists state to ~/.dojozero/
- Supports strategy plugins for automated betting
- Exposes Unix socket RPC for CLI commands
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import asdict, dataclass, field
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


def _write_results(
    results_file: Path,
    trial_id: str,
    reason: str,
    results: list[AgentResult],
) -> None:
    """Write final trial results to results.json."""
    data = {
        "trial_id": trial_id,
        "status": reason,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "results": [asdict(r) for r in results],
    }
    with open(results_file, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Trial %s: Results written to %s", trial_id, results_file)


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
        strategy: str | None = None,
        strategy_config: dict[str, Any] | None = None,
        auto_bet: bool = False,
    ):
        """Initialize trial handler.

        Args:
            trial_id: Trial identifier
            api_key: API key for authentication
            client: Shared DojoClient instance
            filters: Event type filters
            strategy: Strategy module path (e.g., "dojozero_client._strategy.conservative")
            strategy_config: Configuration dict to pass to strategy
            auto_bet: Enable autonomous betting with strategy
        """
        self.trial_id = trial_id
        self.api_key = api_key
        self.client = client
        self.filters = filters or ["event.*", "odds.*"]
        self._strategy_path = strategy
        self._strategy_config = strategy_config or {}
        self.auto_bet = auto_bet

        self.state_dir = TRIALS_DIR / trial_id
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self._state = DaemonState(trial_id=trial_id)
        self._trial: "TrialConnection | None" = None
        self._context_manager: Any = None  # The async context manager
        self._event_task: asyncio.Task[None] | None = None
        self._running = False
        self._seen_uids: set[str] = set()
        self._needs_balance_refresh = False
        self.strategy: Strategy | None = None

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

        # Load strategy plugin if configured
        if self._strategy_path:
            self.strategy = self._load_strategy(
                self._strategy_path, self._strategy_config
            )

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

    async def unregister_from_server(self) -> dict[str, Any]:
        """Unregister agent from the server (DELETE /agents/{agent_id}).

        WARNING: This deletes the broker account — balance and bets are lost.
        """
        if self._trial:
            return await self._trial.unregister()
        # No active connection — use stored state for a direct call
        agent_id = self._state.agent_id
        session_key = self._state.session_key
        if not agent_id or not session_key:
            raise RPCError(
                "NO_STATE",
                "No agent_id or session_key available for unregistration",
            )
        from dojozero_client._config import load_config

        gateway_url = load_config().get_gateway_url(self.trial_id)
        return await DojoClient.unregister_agent(gateway_url, agent_id, session_key)

    async def place_bet(
        self,
        amount: float,
        market: str,
        selection: str,
        spread_value: float | None = None,
        total_value: float | None = None,
    ) -> dict[str, Any]:
        """Place a bet on this trial.

        Args:
            amount: Bet amount
            market: Market type (moneyline, spread, total)
            selection: Selection (home, away, over, under)
            spread_value: Spread value for spread bets (e.g., -3.5)
            total_value: Total value for total bets (e.g., 215.5)

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
            spread_value=spread_value,
            total_value=total_value,
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
                    _write_results(
                        self.state_dir / "results.json",
                        self.trial_id,
                        ended.reason,
                        ended.final_results,
                    )
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

        # Maybe make betting decision (auto-bet with strategy)
        if self.auto_bet and self.strategy and self._trial:
            try:
                decision = self.strategy.decide(event_dict, self._state.to_dict())
                if decision:
                    result = await self._trial.place_bet(
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
                    logger.info(
                        "Trial %s: Auto-bet %s on %s for $%s",
                        self.trial_id,
                        decision["market"],
                        decision["selection"],
                        decision["amount"],
                    )
                    await self._refresh_balance_after_bet(
                        result.amount, result.market, result.selection
                    )
            except Exception as e:
                logger.warning(
                    "Trial %s: Strategy decision error: %s", self.trial_id, e
                )

    def _update_state_from_event(self, event: dict[str, Any]) -> None:
        """Update state from an event."""
        payload = event.get("payload", {})
        # Use the inner event_type (e.g., "event.odds_update") not the
        # outer SSE type (always "event").
        event_type = payload.get("event_type", "") or event.get("type", "")
        sequence = event.get("sequence", 0)

        if sequence > self._state.last_event_sequence:
            self._state.last_event_sequence = sequence

        # Update game state from game/play events
        if "game" in event_type.lower() or "play" in event_type.lower():
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

        # Update balance from balance events
        if "balance" in event_type.lower():
            self._state.balance = payload.get("balance", self._state.balance)

        self._save_state()

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
            logger.error(
                "Trial %s: Failed to load strategy %s: %s",
                self.trial_id,
                module_path,
                e,
            )
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
        self,
        trial_id: str,
        filters: list[str] | None = None,
        strategy: str | None = None,
        strategy_config: dict[str, Any] | None = None,
        auto_bet: bool = False,
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
            strategy=strategy,
            strategy_config=strategy_config,
            auto_bet=auto_bet,
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

    async def _handle_leave(
        self, trial_id: str, unregister: bool = False
    ) -> dict[str, Any]:
        """Leave a trial, optionally unregistering from the server."""
        if trial_id not in self._trials:
            raise RPCError("NOT_FOUND", f"Not connected to trial {trial_id}")

        handler = self._trials.pop(trial_id)
        if unregister:
            try:
                await handler.unregister_from_server()
            except Exception as e:
                logger.warning(
                    "Server unregister failed (continuing local disconnect): %s", e
                )
        await handler.disconnect()
        return {"status": "left", "unregistered": unregister}

    async def _handle_bet(
        self,
        trial_id: str,
        amount: float,
        market: str,
        selection: str,
        spread_value: float | None = None,
        total_value: float | None = None,
    ) -> dict[str, Any]:
        """Place a bet."""
        handler = self._get_handler(trial_id)
        return await handler.place_bet(
            amount,
            market,
            selection,
            spread_value=spread_value,
            total_value=total_value,
        )

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
    "DaemonState",
    "Strategy",
    "get_daemon_status",
    "UnifiedDaemon",
    "TrialHandler",
    "is_daemon_running",
    "stop_daemon",
]


# Convenience aliases (renamed from is_unified_daemon_running / stop_unified_daemon)
is_daemon_running = is_unified_daemon_running
stop_daemon = stop_unified_daemon
