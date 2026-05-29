"""ContestOperator protocol — unified interface for all contest types.

Both :class:`BrokerOperator` (classic betting) and :class:`PredictionBroker`
(prediction contest) implement this protocol so that the gateway, adapter,
and dashboard layers can operate polymorphically without ``isinstance``
checks.

A third scoring system (e.g. Brier-score, weighted-stake) only needs to
implement this protocol to become a drop-in contest type.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from dojozero.betting._models import ContestEvent


@runtime_checkable
class ContestOperator(Protocol):
    """Minimal interface that every contest broker must satisfy."""

    actor_id: str
    trial_id: str

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    # -- current event ------------------------------------------------------

    @property
    def current_event(self) -> Optional[ContestEvent]:
        """The contest event currently tracked, or ``None`` before bootstrap.

        Replaces direct access to the private ``_event`` attribute so callers
        don't depend on internal storage layout.
        """
        ...

    # -- rules & discovery --------------------------------------------------

    def get_rules(self) -> Dict[str, Any]:
        """Return a structured rule descriptor for this contest type.

        Must include at least ``{"kind": "<contest_kind>", ...}``.
        """
        ...

    def get_contest_kind(self) -> str:
        """Short identifier: ``"classic_betting"``, ``"window_pool_prediction"``, etc."""
        ...

    # -- accepting state ----------------------------------------------------

    def is_accepting(self) -> bool:
        """True when the contest is open for submissions."""
        ...

    # -- event bootstrap (checkpoint recovery) ------------------------------

    def ensure_event_initialized(
        self,
        event_id: str,
        home_team: str,
        away_team: str,
        game_time: Optional[datetime] = None,
    ) -> bool:
        """Create a fallback event from metadata when none exists yet.

        Returns ``True`` if a new event was created.
        """
        ...

    # -- agent registration -------------------------------------------------

    @property
    def agents(self) -> tuple[str, ...]:
        """IDs of all registered agents."""
        ...

    # -- persistence --------------------------------------------------------

    async def save_state(self) -> Dict[str, Any]: ...
    async def load_state(self, state: Dict[str, Any]) -> None: ...


__all__ = ["ContestOperator"]
