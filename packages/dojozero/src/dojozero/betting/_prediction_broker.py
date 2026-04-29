"""Prediction Broker Operator.

This operator implements a standalone prediction contest, decoupled from
the classic betting :class:`BrokerOperator`. It tracks the lifecycle of a
single sports event and lets agents submit discrete win/lose/even
predictions in five fixed windows (pre-game and Q1-Q4). Each agent may
submit at most one prediction per window per event. After the event
settles, every correct prediction in window ``w`` shares
``window_pools[w]`` evenly with the other correct predictions in that
window.

Compared to :class:`BrokerOperator`, this operator deliberately omits
account balances, bet placement, odds tracking, and limit-order matching.
It only needs the game lifecycle to determine the current window.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, Sequence, TypedDict

from pydantic import TypeAdapter

from dojozero.betting._models import (
    BettingEvent,
    EventStatus,
    Prediction,
    PredictionOutcome,
    PredictionStatistics,
    VALID_STATUS_TRANSITIONS,
)
from dojozero.betting._scoring import (
    DEFAULT_WINDOW_POOLS,
    NUM_WINDOWS,
    settle_window_predictions,
)
from dojozero.core import (
    Agent,
    Operator,
    OperatorBase,
    RuntimeContext,
    StreamEvent,
)
from dojozero.core._tracing import create_span_from_event, emit_span
from dojozero.data._models import (
    BaseGameUpdateEvent,
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
)


logger = logging.getLogger(__name__)


# Sport-specific regulation defaults: (regulation_periods, seconds_per_period)
_SPORT_CLOCK_DEFAULTS: Dict[str, tuple[int, int]] = {
    "nba": (4, 12 * 60),
    "nfl": (4, 15 * 60),
    "ncaa": (2, 20 * 60),
}


class _ActorIdConfig(TypedDict):
    actor_id: str


class PredictionBrokerConfig(_ActorIdConfig, total=False):
    """Configuration for :class:`PredictionBroker`."""

    window_pools: list[int]
    allowed_tools: list[str]


# =============================================================================
# Helpers
# =============================================================================


def _parse_clock_to_seconds(clock: str, default_seconds: int) -> int:
    """Parse MM:SS game clock string to remaining seconds in the period."""
    if not clock:
        return default_seconds
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", clock)
    if not m:
        return default_seconds
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    value = minutes * 60 + seconds
    return max(0, min(default_seconds, value))


def _format_rules(window_pools: list[int]) -> str:
    """Render the contest rules as a human-readable string for agents."""
    pool_lines = [
        f"  - Window 0 (Pre-game): {window_pools[0]}",
        f"  - Window 1 (Q1):       {window_pools[1]}",
        f"  - Window 2 (Q2):       {window_pools[2]}",
        f"  - Window 3 (Q3):       {window_pools[3]}",
        f"  - Window 4 (Q4):       {window_pools[4]}",
    ]
    pools_block = "\n".join(pool_lines)
    return (
        "PREDICTION CONTEST RULES\n"
        "------------------------\n"
        "The game is split into FIVE prediction windows. In each window, you may\n"
        "submit AT MOST ONE prediction per event using `submit_prediction`.\n"
        "If your prediction is correct, you share that window's prize pool with\n"
        "every other agent who is also correct in the same window.\n\n"
        f"Prize pools per window:\n{pools_block}\n\n"
        "Selections (`selection` argument of `submit_prediction`):\n"
        "  - 'home_win' : home team wins\n"
        "  - 'away_win' : away team wins\n"
        "  - 'even'     : tie / no clear winner\n\n"
        "Window assignment:\n"
        "  - Pre-game (status=SCHEDULED) submissions go to window 0.\n"
        "  - During play, the broker assigns the window from the current period\n"
        "    (1=Q1, 2=Q2, 3=Q3, 4=Q4). Overtime maps to window 4.\n"
        "  - You can resubmit in the same window at any time before the event\n"
        "    closes; the latest submission replaces the previous one.\n\n"
        "Strategy:\n"
        "  - Earlier windows pay more, but the outcome is less certain.\n"
        "  - Window 4 has a much smaller pool because by Q4 the result is\n"
        "    typically clear.\n"
        "  - Contrarian correct picks (when few agents pick your side) earn a\n"
        "    larger share of the pool.\n"
    )


# =============================================================================
# Prediction Broker Operator
# =============================================================================


class PredictionBroker(OperatorBase, Operator[PredictionBrokerConfig]):
    """Sport-agnostic prediction broker.

    Subscribes to ``game_lifecycle_stream`` and ``game_update_stream`` to
    track a single event's status and current period. Exposes prediction
    tools to registered agents.
    """

    def __init__(self, config: PredictionBrokerConfig, trial_id: str):
        super().__init__(config["actor_id"], trial_id)

        self._event: Optional[BettingEvent] = None
        self._event_lock: asyncio.Lock = asyncio.Lock()

        # Buffer events that arrive before GameInitializeEvent.
        self._pending_status_events: Dict[
            str, List[tuple[str, GameStartEvent | GameResultEvent]]
        ] = defaultdict(list)

        # Cache the most recent game update so we can derive period and
        # elapsed ratio when an agent submits a prediction with the default
        # ``window`` argument.
        self._recent_game_updates: deque[BaseGameUpdateEvent] = deque(maxlen=1)

        # Predictions and per-(agent, window) submission tracking.
        self._predictions: Dict[str, Prediction] = {}
        # agent_id -> event_id -> set[window]
        self._submitted_windows: Dict[str, Dict[str, set[int]]] = defaultdict(
            lambda: defaultdict(set)
        )

        pools = list(config.get("window_pools") or DEFAULT_WINDOW_POOLS)
        if len(pools) != NUM_WINDOWS:
            raise ValueError(
                f"window_pools must have exactly {NUM_WINDOWS} entries, got {len(pools)}"
            )
        self._window_pools: list[int] = pools

        self.allowed_tools = config.get("allowed_tools", None)

        self._state_snapshot_lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    def from_dict(
        cls, config: PredictionBrokerConfig, context: RuntimeContext
    ) -> "PredictionBroker":
        return cls(config, context.trial_id)

    async def start(self) -> None:
        logger.info(
            "PredictionBroker '%s' starting (event=%s, predictions=%d, pools=%s)",
            self.actor_id,
            "present" if self._event else "none",
            len(self._predictions),
            self._window_pools,
        )

    async def stop(self) -> None:
        logger.info(
            "PredictionBroker '%s' stopping (predictions=%d)",
            self.actor_id,
            len(self._predictions),
        )

    async def register_agents(self, agents: Sequence[Agent]) -> None:
        """Register agents (no account creation -- this broker is account-less)."""
        super().register_agents(agents)

    # =========================================================================
    # Event Stream Processing
    # =========================================================================

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Dispatch incoming stream events to typed handlers."""
        try:
            data_event = event.payload
            if not hasattr(data_event, "game_id"):
                logger.warning(
                    "Event missing game_id attribute: type=%s",
                    type(data_event),
                )
                return

            event_id = data_event.game_id
            if not event_id:
                logger.error("Event missing game_id: %s", data_event)
                return

            async with self._event_lock:
                event_type_str = getattr(data_event, "event_type", "unknown")
                logger.info(
                    "PredictionBroker received event: type=%s, event_id=%s, stream_id=%s",
                    event_type_str,
                    event_id,
                    event.stream_id,
                )

                if isinstance(data_event, GameInitializeEvent):
                    await self._handle_game_initialize(data_event, event_id)
                elif isinstance(data_event, GameResultEvent):
                    await self._handle_game_result(data_event, event_id)
                elif isinstance(data_event, GameStartEvent):
                    await self._handle_game_start(data_event, event_id)
                elif isinstance(data_event, BaseGameUpdateEvent):
                    await self._handle_game_update(data_event, event_id)
                else:
                    logger.debug(
                        "PredictionBroker: unhandled event type %s",
                        event_type_str,
                    )
        except Exception as e:
            logger.error(
                "PredictionBroker failed to handle event: %s", e, exc_info=True
            )

    async def _handle_game_initialize(
        self, data_event: GameInitializeEvent, event_id: str
    ) -> None:
        home_team_str = str(data_event.home_team)
        away_team_str = str(data_event.away_team)
        game_time_dt = data_event.game_time

        if not home_team_str or not away_team_str:
            logger.warning(
                "GameInitializeEvent missing team info: event_id=%s",
                event_id,
            )
            return

        if self._event is not None:
            self._event.home_team = home_team_str
            self._event.away_team = away_team_str
            self._event.game_time = game_time_dt
            return

        self._event = BettingEvent(
            event_id=event_id,
            home_team=home_team_str,
            away_team=away_team_str,
            game_time=game_time_dt,
            status=EventStatus.SCHEDULED,
            home_probability=None,
            away_probability=None,
            last_odds_update=None,
        )
        logger.info(
            "PredictionBroker initialized event %s: %s vs %s",
            event_id,
            home_team_str,
            away_team_str,
        )
        await self._apply_pending_status_events(event_id)

    async def _apply_pending_status_events(self, event_id: str) -> None:
        if event_id not in self._pending_status_events:
            return
        pending_events = self._pending_status_events.pop(event_id)
        for event_type, data_event in pending_events:
            try:
                if event_type == "game_start":
                    await self._update_event_status(event_id, EventStatus.LIVE)
                elif event_type == "game_result" and isinstance(
                    data_event, GameResultEvent
                ):
                    await self._update_event_status(event_id, EventStatus.CLOSED)
                    await self._settle_event(event_id, data_event.winner)
            except Exception as e:
                logger.error(
                    "PredictionBroker failed to apply buffered %s for %s: %s",
                    event_type,
                    event_id,
                    e,
                )

    async def _handle_game_update(
        self, data_event: BaseGameUpdateEvent, event_id: str
    ) -> None:
        self._recent_game_updates.append(data_event)

        # If team info hasn't arrived via GameInitializeEvent, opportunistically
        # back-fill from the update's team stats so `get_event_info` is useful.
        if self._event is None:
            home_team_str = self._extract_team_name(data_event, side="home")
            away_team_str = self._extract_team_name(data_event, side="away")
            if not (home_team_str and away_team_str):
                return
            game_time_dt: Optional[datetime] = None
            if data_event.game_time_utc:
                try:
                    game_time_dt = datetime.fromisoformat(
                        data_event.game_time_utc.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    game_time_dt = None
            self._event = BettingEvent(
                event_id=event_id,
                home_team=home_team_str,
                away_team=away_team_str,
                game_time=game_time_dt or datetime.now(),
                status=EventStatus.SCHEDULED,
                home_probability=None,
                away_probability=None,
                last_odds_update=None,
            )
            logger.info(
                "PredictionBroker initialized event %s from game update",
                event_id,
            )

    @staticmethod
    def _extract_team_name(
        data_event: BaseGameUpdateEvent, *, side: Literal["home", "away"]
    ) -> Optional[str]:
        attr = "home_team_stats" if side == "home" else "away_team_stats"
        if not hasattr(data_event, attr):
            return None
        stats = getattr(data_event, attr)
        name = getattr(stats, "team_name", "")
        city = getattr(stats, "team_city", "")
        if city and name:
            return f"{city} {name}".strip()
        return name or None

    async def _handle_game_start(
        self, data_event: GameStartEvent, event_id: str
    ) -> None:
        if self._event is None:
            self._pending_status_events[event_id].append(("game_start", data_event))
            return
        await self._update_event_status(event_id, EventStatus.LIVE)

    async def _handle_game_result(
        self, data_event: GameResultEvent, event_id: str
    ) -> None:
        if self._event is None:
            self._pending_status_events[event_id].append(("game_result", data_event))
            return
        await self._update_event_status(event_id, EventStatus.CLOSED)
        await self._settle_event(event_id, data_event.winner)

    async def _update_event_status(self, event_id: str, status: EventStatus) -> None:
        if self._event is None:
            raise ValueError(f"Event {event_id} not found")
        if self._event.status == status:
            return
        valid = VALID_STATUS_TRANSITIONS.get(self._event.status, set())
        if status not in valid:
            raise ValueError(
                f"Invalid status transition: {self._event.status.value} -> {status.value}"
            )
        logger.info(
            "PredictionBroker event %s status: %s -> %s",
            event_id,
            self._event.status.value,
            status.value,
        )
        self._event.status = status
        if status == EventStatus.CLOSED:
            self._event.betting_closed_at = datetime.now()

    async def _settle_event(self, event_id: str, winner: str) -> None:
        if self._event is None:
            raise ValueError(f"Event {event_id} not found")
        if self._event.status != EventStatus.CLOSED:
            raise ValueError(
                f"Cannot settle event with status {self._event.status.value}, must be CLOSED"
            )
        if winner not in ("home", "away"):
            raise ValueError(f"Invalid winner: {winner}")

        predictions_for_event = [
            p for p in self._predictions.values() if p.event_id == event_id
        ]
        if predictions_for_event:
            settled = settle_window_predictions(
                predictions_for_event, winner, self._window_pools
            )
            for p in settled:
                self._predictions[p.prediction_id] = p
            logger.info(
                "PredictionBroker settled %d predictions for event %s (winner=%s)",
                len(settled),
                event_id,
                winner,
            )
        else:
            logger.info(
                "PredictionBroker has no predictions to settle for event %s",
                event_id,
            )

        self._event.status = EventStatus.SETTLED
        await self._log_final_stats()

    # =========================================================================
    # Window / elapsed-ratio computation
    # =========================================================================

    def _resolve_window(self, requested_window: Optional[int]) -> int:
        """Resolve which contest window applies to a submission.

        ``requested_window`` is the value supplied by the agent. If it is
        ``None`` (the default), we infer from event status:
            - SCHEDULED -> 0 (pre-game)
            - LIVE      -> max(1, period from latest game update), capped at 4
            - CLOSED/SETTLED -> 4 (last regulation window)
        Explicit values 0-4 are clamped to that range and returned as-is;
        anything else is rejected by the caller.
        """
        if requested_window is not None:
            return requested_window

        if self._event is None:
            return 0
        if self._event.status == EventStatus.SCHEDULED:
            return 0
        if self._event.status in (EventStatus.CLOSED, EventStatus.SETTLED):
            return NUM_WINDOWS - 1
        if not self._recent_game_updates:
            return 0
        update = self._recent_game_updates[-1]
        period = int(getattr(update, "period", 0) or 0)
        if period <= 0:
            return 0
        return min(period, NUM_WINDOWS - 1)

    def _compute_elapsed_ratio(self) -> float:
        """Compute game progress in [0.0, 1.0] from the latest game update."""
        if self._event is None or self._event.status == EventStatus.SCHEDULED:
            return 0.0
        if self._event.status in (EventStatus.CLOSED, EventStatus.SETTLED):
            return 1.0
        if not self._recent_game_updates:
            return 0.0

        update = self._recent_game_updates[-1]
        period = max(0, int(getattr(update, "period", 0) or 0))
        game_clock = str(getattr(update, "game_clock", "") or "")
        sport = str(getattr(update, "sport", "") or "").lower()

        regulation_periods, seconds_per_period = _SPORT_CLOCK_DEFAULTS.get(
            sport, (4, 15 * 60)
        )
        total = regulation_periods * seconds_per_period
        if period <= 0 or total <= 0:
            return 0.0

        remaining = _parse_clock_to_seconds(game_clock, seconds_per_period)
        elapsed_periods = max(0, period - 1)
        elapsed_seconds = elapsed_periods * seconds_per_period + (
            seconds_per_period - remaining
        )
        return max(0.0, min(1.0, elapsed_seconds / total))

    # =========================================================================
    # Prediction submission
    # =========================================================================

    async def submit_prediction(
        self,
        agent_id: str,
        event_id: str,
        selection: str,
        window: Optional[int] = None,
    ) -> str:
        """Submit a prediction. Returns ``"prediction_submitted"`` or an error."""
        if self._event is None or self._event.event_id != event_id:
            return "prediction_error: Invalid event ID"

        try:
            prediction_outcome = PredictionOutcome(selection.lower())
        except ValueError:
            return (
                f"prediction_error: Invalid selection '{selection}'. "
                "Valid options: 'home_win', 'away_win', 'even'"
            )

        if window is not None and not (0 <= window < NUM_WINDOWS):
            return f"prediction_error: window must be in [0, {NUM_WINDOWS - 1}], got {window}"

        auto_window = self._resolve_window(None)
        if window is not None and window != auto_window:
            return (
                f"prediction_error: explicit window {window} does not match "
                f"current game window {auto_window}"
            )
        resolved_window = auto_window

        if self._event.status == EventStatus.SETTLED:
            return "prediction_error: Event already settled"
        if self._event.status == EventStatus.CLOSED:
            return "prediction_error: Event closed for predictions"

        # If a prediction already exists for this window, remove it so the new
        # submission replaces it (last-write-wins per window).
        already_used = self._submitted_windows[agent_id][event_id]
        if resolved_window in already_used:
            old_id = next(
                pid
                for pid, p in self._predictions.items()
                if p.agent_id == agent_id
                and p.event_id == event_id
                and p.window == resolved_window
            )
            del self._predictions[old_id]
            logger.info(
                "PredictionBroker replaced prediction %s (agent=%s, window=%d)",
                old_id,
                agent_id,
                resolved_window,
            )

        prediction_id = f"pred_{uuid.uuid4().hex[:12]}"
        prediction = Prediction(
            prediction_id=prediction_id,
            agent_id=agent_id,
            event_id=event_id,
            selection=prediction_outcome,
            submit_time=datetime.now(),
            window=resolved_window,
            elapsed_ratio=self._compute_elapsed_ratio(),
            is_correct=None,
            score=None,
        )
        self._predictions[prediction_id] = prediction
        self._submitted_windows[agent_id][event_id].add(resolved_window)

        logger.info(
            "PredictionBroker prediction %s - agent=%s, selection=%s, window=%d, elapsed=%.4f",
            prediction_id,
            agent_id,
            selection,
            resolved_window,
            prediction.elapsed_ratio,
        )
        return "prediction_submitted"

    async def get_my_predictions(
        self, agent_id: str, event_id: str | None = None
    ) -> list[Prediction]:
        return [
            p
            for p in self._predictions.values()
            if p.agent_id == agent_id and (event_id is None or p.event_id == event_id)
        ]

    async def get_event_info(self) -> Optional[Dict[str, Any]]:
        """Return a serializable snapshot of the current event state.

        Includes status, current window, elapsed ratio, latest period and
        scoreboard (when available) so agents can decide when to submit.
        """
        if self._event is None:
            return None

        info: Dict[str, Any] = {
            "event_id": self._event.event_id,
            "home_team": self._event.home_team,
            "away_team": self._event.away_team,
            "game_time": self._event.game_time.isoformat()
            if self._event.game_time
            else None,
            "status": self._event.status.value,
            "current_window": self._resolve_window(None),
            "elapsed_ratio": round(self._compute_elapsed_ratio(), 4),
        }
        if self._recent_game_updates:
            update = self._recent_game_updates[-1]
            info["period"] = int(getattr(update, "period", 0) or 0)
            info["game_clock"] = getattr(update, "game_clock", None)
            home_stats = getattr(update, "home_team_stats", None)
            away_stats = getattr(update, "away_team_stats", None)
            if home_stats is not None:
                info["home_score"] = getattr(home_stats, "points", None)
            if away_stats is not None:
                info["away_score"] = getattr(away_stats, "points", None)
        return info

    def get_rules(self) -> Dict[str, Any]:
        """Return the prediction contest rules (used by tools and gateway)."""
        return {
            "kind": "window_pool_prediction",
            "num_windows": NUM_WINDOWS,
            "window_pools": list(self._window_pools),
            "windows": [
                {"index": 0, "label": "Pre-game", "pool": self._window_pools[0]},
                {"index": 1, "label": "Q1", "pool": self._window_pools[1]},
                {"index": 2, "label": "Q2", "pool": self._window_pools[2]},
                {"index": 3, "label": "Q3", "pool": self._window_pools[3]},
                {"index": 4, "label": "Q4", "pool": self._window_pools[4]},
            ],
            "selections": ["home_win", "away_win", "even"],
            "max_predictions_per_window": "unlimited (last submission wins)",
            "scoring": (
                "share_of_window_pool: each correct prediction in a window splits "
                "that window's pool equally among all correct predictions; "
                "incorrect predictions earn 0."
            ),
            "description": _format_rules(self._window_pools),
        }

    # =========================================================================
    # State persistence
    # =========================================================================

    async def save_state(self) -> Dict[str, Any]:
        async with self._event_lock:
            return {
                "actor_id": self.actor_id,
                "event": self._event.model_dump(mode="json")
                if self._event is not None
                else None,
                "window_pools": list(self._window_pools),
                "predictions": {
                    pid: p.model_dump(mode="json")
                    for pid, p in self._predictions.items()
                },
                "submitted_windows": {
                    agent_id: {
                        eid: sorted(list(windows)) for eid, windows in by_event.items()
                    }
                    for agent_id, by_event in self._submitted_windows.items()
                },
                "recent_game_updates": [
                    u.model_dump(mode="json") for u in self._recent_game_updates
                ],
            }

    async def load_state(self, state: Dict[str, Any]) -> None:
        if state.get("event") is not None:
            self._event = BettingEvent.model_validate(state["event"])
        else:
            self._event = None

        if "window_pools" in state and state["window_pools"]:
            pools = list(state["window_pools"])
            if len(pools) == NUM_WINDOWS:
                self._window_pools = pools

        self._predictions = {
            pid: Prediction.model_validate(pdata)
            for pid, pdata in state.get("predictions", {}).items()
        }

        submitted: Dict[str, Dict[str, set[int]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for agent_id, by_event in state.get("submitted_windows", {}).items():
            for eid, windows in by_event.items():
                submitted[agent_id][eid] = set(windows)
        self._submitted_windows = submitted

        if "recent_game_updates" in state:
            from dojozero.data import deserialize_data_event

            restored: list[BaseGameUpdateEvent] = []
            for u in state["recent_game_updates"]:
                event = deserialize_data_event(u)
                if isinstance(event, BaseGameUpdateEvent):
                    restored.append(event)
            self._recent_game_updates = deque(restored, maxlen=1)

    # =========================================================================
    # Tracing
    # =========================================================================

    async def _log_final_stats(self) -> None:
        """Emit a ``broker.final_stats`` span with prediction statistics.

        We reuse :class:`BrokerFinalStats` (and the ``broker.final_stats``
        operation name) so existing arena_server leaderboard logic works
        without modification. Account/bet fields are left empty.
        """
        async with self._state_snapshot_lock:
            current_event_predictions = (
                {
                    pid: p
                    for pid, p in self._predictions.items()
                    if self._event is None or p.event_id == self._event.event_id
                }
                if self._event is not None
                else {}
            )

            prediction_stats: Dict[str, PredictionStatistics] = {}
            agents_seen: set[str] = set()
            for p in current_event_predictions.values():
                agents_seen.add(p.agent_id)

            for agent_id in agents_seen:
                agent_predictions = [
                    p
                    for p in current_event_predictions.values()
                    if p.agent_id == agent_id
                ]
                if not agent_predictions:
                    continue
                total = len(agent_predictions)
                correct = sum(1 for p in agent_predictions if p.is_correct)
                accuracy = correct / total if total > 0 else 0.0
                total_score = sum(
                    float(p.score) if p.score is not None else 0.0
                    for p in agent_predictions
                )
                prediction_stats[agent_id] = PredictionStatistics(
                    total_predictions=total,
                    correct_predictions=correct,
                    accuracy=accuracy,
                    total_score=Decimal(str(total_score)),
                )

            preds_adapter = TypeAdapter(Dict[str, Prediction])
            pred_stats_adapter = TypeAdapter(Dict[str, PredictionStatistics])

            tags: Dict[str, Any] = {
                "broker.kind": "prediction",
                "broker.window_pools": json.dumps(list(self._window_pools)),
                "broker.predictions": preds_adapter.dump_json(
                    current_event_predictions
                ).decode(),
                "broker.prediction_statistics": pred_stats_adapter.dump_json(
                    prediction_stats
                ).decode(),
            }

            span = create_span_from_event(
                trial_id=self.trial_id,
                actor_id=self.actor_id,
                operation_name="broker.final_stats",
                extra_tags=tags,
            )
            emit_span(span)

    # =========================================================================
    # Agent Tools
    # =========================================================================

    def agent_tools(
        self, agent_id: str, operator: "PredictionBroker | None" = None
    ) -> list:
        """Return tool functions bound to ``agent_id`` for toolkit registration."""
        from dojozero.agents._toolkit import tool  # type: ignore[import-untyped]

        target = operator if operator is not None else self
        allowed_tools = getattr(self, "allowed_tools", None)
        allowed_tools_set = (
            {name.lower() for name in allowed_tools} if allowed_tools else None
        )

        @tool
        async def get_rules() -> str:
            """Get the prediction contest rules.

            Call this at the start of the event to learn the windows, prize
            pools, valid selections, and scoring formula. The returned
            object also includes a human-readable ``description`` block.

            Returns:
                JSON object describing the contest rules.
            """
            rules = target.get_rules()
            return json.dumps(rules)

        @tool
        async def get_event_info() -> str:
            """Get the current event status and progress.

            Returns:
                JSON object with event_id, teams, status, current_window,
                elapsed_ratio, period, game_clock, and scores when available.
                ``"null"`` if no event is registered yet.
            """
            info = await target.get_event_info()
            if info is None:
                return "null"
            return json.dumps(info)

        _VALID_SELECTIONS = {"home_win", "away_win", "even"}

        @tool
        async def submit_prediction(
            selection: str,
        ) -> str:
            """Submit a prediction for the current event.

            The broker automatically assigns the prediction to the current
            contest window based on live game state (0 = pre-game, 1-4 = Q1-Q4).
            If you submit again in the same window, the new prediction replaces
            the previous one.

            Args:
                selection: "home_win", "away_win", or "even".

            Returns:
                "prediction_submitted" or "prediction_error: <reason>".
            """
            if selection not in _VALID_SELECTIONS:
                return (
                    f"prediction_error: Invalid selection '{selection}'. "
                    f"Must be one of: {', '.join(sorted(_VALID_SELECTIONS))}"
                )
            event = target._event  # type: ignore[attr-defined]
            if event is None:
                return "prediction_error: No active event available"
            try:
                return await target.submit_prediction(
                    agent_id, event.event_id, selection
                )
            except Exception as e:
                logger.error(
                    "Unexpected error in submit_prediction: %s",
                    e,
                    exc_info=True,
                )
                return f"prediction_error: Unexpected error - {str(e)}"

        @tool
        async def get_my_predictions() -> str:
            """List your predictions for the current event with scores when settled.

            Returns:
                JSON array of predictions, each with prediction_id, selection,
                window, submit_time, elapsed_ratio, is_correct (after settlement),
                and score (after settlement).
            """
            event = target._event  # type: ignore[attr-defined]
            event_id = event.event_id if event is not None else None
            try:
                preds = await target.get_my_predictions(agent_id, event_id)
                preds_adapter = TypeAdapter(List[Prediction])
                return preds_adapter.dump_json(preds).decode()
            except Exception as e:
                logger.error(
                    "Unexpected error in get_my_predictions: %s",
                    e,
                    exc_info=True,
                )
                return f"predictions_error: Unexpected error - {str(e)}"

        all_tools_map = {
            "get_rules": get_rules,
            "get_event_info": get_event_info,
            "submit_prediction": submit_prediction,
            "get_my_predictions": get_my_predictions,
        }

        if allowed_tools_set is None:
            return list(all_tools_map.values())
        return [
            tool_func
            for name, tool_func in all_tools_map.items()
            if name.lower() in allowed_tools_set
        ]


__all__ = [
    "PredictionBroker",
    "PredictionBrokerConfig",
]
