"""Regression tests for the gateway backtest replay launch path.

These guard the two crashers fixed alongside World Cup arena support, where
``POST /api/trials`` with a ``backtest`` config was never exercised:

1. ``builder_name`` was read from ``spec.metadata`` instead of the
   ``TrialSpec.builder_name`` field, so a valid backtest always raised
   ``OrchestratorError("builder_name is required in metadata for backtest")``.
"""

from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from dojozero.core import OrchestratorError, TrialBuilderNotFoundError, TrialSpec
from dojozero.dashboard_server._server import _launch_backtest_trial

# The launch path only reads metadata via .get(); a plain dict is a sufficient
# test double. cast keeps the generic TrialSpec[MetadataT] type-checker happy.
_EMPTY_META = cast(Any, {})


class _FakeHub:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def enable_backtest_traces(self, **kwargs) -> None:
        pass


class _FakeCoordinator:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def set_speed(self, **kwargs) -> None:
        pass

    async def start(self) -> None:
        pass


@pytest.fixture
def _patch_backtest_infra(monkeypatch):
    """Stub out DataHub/BacktestCoordinator so the launch path does no I/O.

    They are imported inside ``_launch_backtest_trial`` via
    ``from dojozero.data import ...``, so patching the names on ``dojozero.data``
    is what the function picks up.
    """
    monkeypatch.setattr("dojozero.data.DataHub", _FakeHub)
    monkeypatch.setattr("dojozero.data.BacktestCoordinator", _FakeCoordinator)


@pytest.mark.asyncio
async def test_backtest_launch_requires_builder_name() -> None:
    """No builder_name on the spec nor in metadata -> clear OrchestratorError."""
    spec = TrialSpec(trial_id="bt-no-builder", metadata=_EMPTY_META, builder_name=None)
    with pytest.raises(OrchestratorError, match="builder_name"):
        await _launch_backtest_trial(
            orchestrator=MagicMock(),
            spec=spec,
            event_file=Path("/nonexistent/events.jsonl"),
            speed=1.0,
            max_sleep=1.0,
        )


@pytest.mark.asyncio
async def test_backtest_launch_uses_spec_builder_name(_patch_backtest_infra) -> None:
    """builder_name from the TrialSpec field is honored even when metadata lacks it.

    The pre-fix code read ``spec.metadata.get("builder_name")`` and would raise
    OrchestratorError here. With the fix it passes the guard and only fails later
    looking up the (intentionally unregistered) builder — a TrialBuilderNotFoundError,
    not the builder_name guard error.
    """
    spec = TrialSpec(
        trial_id="bt-spec-builder",
        metadata=_EMPTY_META,  # deliberately no "builder_name" key
        builder_name="a-builder-that-is-not-registered",
    )
    with pytest.raises(TrialBuilderNotFoundError):
        await _launch_backtest_trial(
            orchestrator=MagicMock(),
            spec=spec,
            event_file=Path("/nonexistent/events.jsonl"),
            speed=1.0,
            max_sleep=1.0,
        )
