"""Tests for MaterializationTracker — background SLS materialization coordinator.

Covers the new 202-polling machinery on the dashboard server:

- start() spawns a task, forwards run_id + progress callback, and uploads on success
- concurrent requests for the same (trial_id, run_id) coalesce onto one task
- distinct run_ids do NOT coalesce
- completed entries are replaced on the next start()
- failures are surfaced via task.exception() (no upload on failure)
- remove() drops the entry; remove() on an absent key is a no-op
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from dojozero.dashboard_server._server import MaterializationTracker


# ---------------------------------------------------------------------------
# Fake SLSEventSource
# ---------------------------------------------------------------------------


class _FakeSource:
    """Stand-in for SLSEventSource driving deterministic test scenarios.

    Signature matches the call site in ``_do_materialize``:
        await SLSEventSource().materialize_jsonl(
            trial_id, cached_path, run_id=..., progress_callback=...
        )
    """

    def __init__(
        self,
        *,
        progress_steps: list[tuple[int, int]] | None = None,
        raises: Exception | None = None,
        started_event: asyncio.Event | None = None,
        proceed_event: asyncio.Event | None = None,
    ) -> None:
        self._progress_steps = progress_steps or []
        self._raises = raises
        self._started_event = started_event
        self._proceed_event = proceed_event
        self.calls: list[tuple[str, Path, str | None]] = []
        self.saw_progress_callback: Callable[[int, int], None] | None = None

    async def materialize_jsonl(
        self,
        trial_id: str,
        dest: Path,
        *,
        run_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        self.calls.append((trial_id, dest, run_id))
        self.saw_progress_callback = progress_callback
        for fetched, total in self._progress_steps:
            if progress_callback is not None:
                progress_callback(fetched, total)
            # Yield so observers can read intermediate state between steps.
            await asyncio.sleep(0)
        if self._started_event is not None:
            self._started_event.set()
        if self._proceed_event is not None:
            await self._proceed_event.wait()
        if self._raises is not None:
            raise self._raises


@pytest.fixture
def patch_backends(monkeypatch: pytest.MonkeyPatch):
    """Install a fake SLSEventSource + upload_trial_to_oss.

    Returns a callable that installs a fake source for the test body and
    returns ``(fake, upload_calls)``.
    """
    upload_calls: list[tuple[str, Path]] = []

    import dojozero.data as data_pkg
    import dojozero.dashboard_server._server as server_mod

    def _install(**kwargs) -> tuple[_FakeSource, list[tuple[str, Path]]]:
        fake = _FakeSource(**kwargs)
        monkeypatch.setattr(data_pkg, "SLSEventSource", lambda: fake)
        monkeypatch.setattr(
            server_mod,
            "upload_trial_to_oss",
            lambda trial_id, path: upload_calls.append((trial_id, path)),
        )
        return fake, upload_calls

    return _install


# ---------------------------------------------------------------------------
# get() basic behavior
# ---------------------------------------------------------------------------


def test_get_returns_none_for_unknown_key() -> None:
    tracker = MaterializationTracker()
    assert tracker.get("nonexistent", None) is None
    assert tracker.get("nonexistent", "some-run") is None


# ---------------------------------------------------------------------------
# start() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_runs_materialize_and_uploads(
    tmp_path: Path, patch_backends
) -> None:
    fake, upload_calls = patch_backends(progress_steps=[(10, 100), (100, 100)])
    tracker = MaterializationTracker()
    dest = tmp_path / "trial-A.jsonl"

    entry = tracker.start("trial-A", None, dest)
    await entry.task

    assert fake.calls == [("trial-A", dest, None)]
    assert upload_calls == [("trial-A", dest)]
    # Entry reflects the last progress tuple after the task drains.
    assert entry.spans_fetched == 100
    assert entry.spans_total == 100
    assert entry.task.exception() is None


@pytest.mark.asyncio
async def test_start_forwards_run_id(tmp_path: Path, patch_backends) -> None:
    fake, _ = patch_backends()
    tracker = MaterializationTracker()
    dest = tmp_path / "trial-A.jsonl"

    entry = tracker.start("trial-A", "rootBig", dest)
    await entry.task

    assert fake.calls == [("trial-A", dest, "rootBig")]


@pytest.mark.asyncio
async def test_entry_started_at_is_monotonic(tmp_path: Path, patch_backends) -> None:
    import time

    patch_backends()
    tracker = MaterializationTracker()
    dest = tmp_path / "trial-A.jsonl"

    before = time.monotonic()
    entry = tracker.start("trial-A", None, dest)
    after = time.monotonic()
    await entry.task

    assert before <= entry.started_at <= after


# ---------------------------------------------------------------------------
# Coalescing: concurrent start() for the same key reuses the in-flight task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_coalesces_concurrent_same_key(
    tmp_path: Path, patch_backends
) -> None:
    started = asyncio.Event()
    proceed = asyncio.Event()
    patch_backends(started_event=started, proceed_event=proceed)
    tracker = MaterializationTracker()
    dest = tmp_path / "trial-A.jsonl"

    entry1 = tracker.start("trial-A", None, dest)
    await started.wait()  # ensure task is actually in-flight
    entry2 = tracker.start("trial-A", None, dest)

    assert entry1 is entry2
    assert entry1.task is entry2.task

    proceed.set()
    await entry1.task


@pytest.mark.asyncio
async def test_start_does_not_coalesce_different_run_ids(
    tmp_path: Path, patch_backends
) -> None:
    patch_backends()
    tracker = MaterializationTracker()
    dest = tmp_path / "trial-A.jsonl"

    entry_a = tracker.start("trial-A", "rootA", dest)
    entry_b = tracker.start("trial-A", "rootB", dest)

    assert entry_a is not entry_b
    assert entry_a.task is not entry_b.task

    await asyncio.gather(entry_a.task, entry_b.task)


# ---------------------------------------------------------------------------
# Replacement: once a task is done, the next start() creates a fresh one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_replaces_completed_entry(tmp_path: Path, patch_backends) -> None:
    patch_backends()
    tracker = MaterializationTracker()
    dest = tmp_path / "trial-A.jsonl"

    first = tracker.start("trial-A", None, dest)
    await first.task
    assert first.task.done()

    second = tracker.start("trial-A", None, dest)
    assert second is not first
    assert second.task is not first.task

    await second.task


# ---------------------------------------------------------------------------
# Failure: exception surfaces via task.exception(); upload skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_failure_captured_in_task(
    tmp_path: Path, patch_backends
) -> None:
    boom = RuntimeError("SLS said no")
    _, upload_calls = patch_backends(raises=boom)
    tracker = MaterializationTracker()
    dest = tmp_path / "trial-A.jsonl"

    entry = tracker.start("trial-A", None, dest)
    # Consume the exception so it doesn't surface as an unhandled-task warning.
    await asyncio.gather(entry.task, return_exceptions=True)

    assert entry.task.done()
    exc = entry.task.exception()
    assert isinstance(exc, RuntimeError)
    assert str(exc) == "SLS said no"
    # Upload happens AFTER materialize; a failed materialize must not upload.
    assert upload_calls == []


# ---------------------------------------------------------------------------
# Progress callback wiring — entry fields mutate mid-flight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_callback_updates_entry_fields_mid_flight(
    tmp_path: Path, patch_backends
) -> None:
    proceed = asyncio.Event()
    fake, _ = patch_backends(progress_steps=[(42, 100)], proceed_event=proceed)
    tracker = MaterializationTracker()
    dest = tmp_path / "trial-A.jsonl"

    entry = tracker.start("trial-A", None, dest)
    # Wait until at least the first progress step has been emitted.
    while entry.spans_fetched == 0 and not entry.task.done():
        await asyncio.sleep(0)

    # The entry — which the endpoint reads on every poll — reflects the
    # latest progress callback invocation.
    assert entry.spans_fetched == 42
    assert entry.spans_total == 100
    assert fake.saw_progress_callback is not None

    proceed.set()
    await entry.task


# ---------------------------------------------------------------------------
# remove()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_drops_entry(tmp_path: Path, patch_backends) -> None:
    patch_backends()
    tracker = MaterializationTracker()
    dest = tmp_path / "trial-A.jsonl"

    entry = tracker.start("trial-A", None, dest)
    await entry.task
    assert tracker.get("trial-A", None) is entry

    tracker.remove("trial-A", None)
    assert tracker.get("trial-A", None) is None


def test_remove_absent_key_is_noop() -> None:
    tracker = MaterializationTracker()
    tracker.remove("nonexistent", None)
    tracker.remove("nonexistent", "some-run")  # also fine
