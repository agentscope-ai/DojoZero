"""Materialize a trial's event stream from Alibaba SLS into a local JSONL file.

SLS stores every event emitted by a trial as an OTel span, keyed by
``_trace_id == trial_id``. This module walks those spans and writes a JSONL
file byte-compatible with ``DataHub._persist_event`` output, so the existing
backtest playback and dedup-on-resume paths work unchanged.

Key behaviors:

- A single ``trace_id`` may contain **multiple runs** (e.g. a trial that was
  double-submitted because the remote scheduler timed out and also ran it
  locally). Each run is a subtree rooted at its own ``trial.started`` span.
  This module partitions spans by run and picks one run — merging two runs
  would interleave two independent histories because most recurring events
  (plays, game updates, odds) do not override ``get_dedup_key()``.
- Auto-selects the "most complete" run (most ``event.*`` spans; tie-break by
  latest ``trial.started`` end time). Callers can override with ``run_id``.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from dojozero.core._tracing import (
    SLSTraceReader,
    SpanData,
    deserialize_event_from_span,
)

if TYPE_CHECKING:
    from dojozero.core._tracing import TraceReader
    from dojozero.data._models import DataEvent

logger = logging.getLogger(__name__)

_TRIAL_STARTED_OP = "trial.started"
_EVENT_OP_PREFIX = "event."


class SLSEventSource:
    """Fetch a trial's event stream from SLS and materialize to JSONL."""

    def __init__(self, reader: "TraceReader | None" = None) -> None:
        """Construct the source.

        Args:
            reader: Optional pre-built TraceReader (used by tests). If None,
                a reader is built from ``DOJOZERO_SLS_*`` env vars at fetch
                time so we fail fast with a clear error when unconfigured.
        """
        self._reader = reader

    async def fetch_events(
        self,
        trial_id: str,
        *,
        run_id: str | None = None,
    ) -> list["DataEvent"]:
        """Fetch and return a trial's events, sorted for playback.

        Args:
            trial_id: Trace id (== trial id in DojoZero).
            run_id: Optional root span id (the ``trial.started`` span id) to
                pick a specific run when the trace contains multiple.

        Returns:
            List of ``DataEvent`` sorted by ``game_timestamp`` when present,
            else ``timestamp``. Matches ``DataHub.start_backtest`` sort key.
        """
        reader = self._reader or _make_reader()
        owns_reader = self._reader is None
        try:
            spans = await reader.get_spans(trial_id)
        finally:
            if owns_reader:
                await reader.close()

        return _spans_to_events(spans, trial_id=trial_id, run_id=run_id)

    async def materialize_jsonl(
        self,
        trial_id: str,
        dest: Path,
        *,
        run_id: str | None = None,
        overwrite: bool = True,
    ) -> Path:
        """Write events for ``trial_id`` to ``dest`` as JSONL.

        The file is written atomically (tempfile + rename). Format is
        ``json.dumps(event.to_dict()) + "\\n"`` per event, matching
        ``DataHub._persist_event``.

        Args:
            trial_id: Trace id.
            dest: Destination JSONL path.
            run_id: See :meth:`fetch_events`.
            overwrite: If False and ``dest`` exists, return it without
                refetching. Default True (always refetch).

        Returns:
            The resolved ``dest`` path.
        """
        dest = Path(dest)
        if not overwrite and dest.exists():
            logger.info("SLS materialize: using cached %s", dest)
            return dest

        events = await self.fetch_events(trial_id, run_id=run_id)

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        try:
            with open(tmp, "w") as f:
                for event in events:
                    f.write(json.dumps(event.to_dict()) + "\n")
            tmp.replace(dest)
        except Exception:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise

        logger.info(
            "SLS materialize: trial=%s wrote %d events to %s",
            trial_id,
            len(events),
            dest,
        )
        return dest


def _make_reader() -> SLSTraceReader:
    """Build an SLSTraceReader from DOJOZERO_SLS_* env vars.

    Raises a clear error if anything is missing. Credentials are resolved
    by the alibabacloud SDK inside the reader — we don't read them here.
    """
    project = os.environ.get("DOJOZERO_SLS_PROJECT", "")
    endpoint = os.environ.get("DOJOZERO_SLS_ENDPOINT", "")
    logstore = os.environ.get("DOJOZERO_SLS_LOGSTORE", "")
    missing = [
        name
        for name, val in (
            ("DOJOZERO_SLS_PROJECT", project),
            ("DOJOZERO_SLS_ENDPOINT", endpoint),
            ("DOJOZERO_SLS_LOGSTORE", logstore),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            "SLSEventSource is not configured; missing env var(s): "
            + ", ".join(missing)
        )
    return SLSTraceReader(endpoint=endpoint, project=project, logstore=logstore)


def _spans_to_events(
    spans: list[SpanData],
    *,
    trial_id: str,
    run_id: str | None,
) -> list["DataEvent"]:
    """Partition by run, pick one, deserialize, sort."""
    if not spans:
        logger.warning("SLS returned zero spans for trial=%s", trial_id)
        return []

    roots = [s for s in spans if s.operation_name == _TRIAL_STARTED_OP]
    if not roots:
        logger.warning(
            "SLS: no trial.started root found for trial=%s; "
            "proceeding with all %d spans (may include cross-run mixing)",
            trial_id,
            len(spans),
        )
        chosen_spans = spans
        chosen_root_id: str | None = None
    else:
        chosen_root_id, chosen_spans = _select_run(
            spans, roots, trial_id=trial_id, run_id=run_id
        )

    events: list["DataEvent"] = []
    for span in chosen_spans:
        if not span.operation_name.startswith(_EVENT_OP_PREFIX):
            continue
        event = deserialize_event_from_span(span)
        if event is None:
            continue
        # Restore event.timestamp from span.start_time (microseconds since
        # epoch). _emit_event_span skips the "timestamp" field when building
        # tags and instead uses it as the span start_time; without this
        # restore, Pydantic's default_factory would stamp datetime.now().
        ts = datetime.fromtimestamp(span.start_time / 1_000_000, tz=timezone.utc)
        event = event.model_copy(update={"timestamp": ts})
        events.append(event)

    events.sort(key=lambda e: e.game_timestamp or e.timestamp)
    logger.info(
        "SLS: trial=%s run=%s kept %d events (from %d spans)",
        trial_id,
        chosen_root_id,
        len(events),
        len(chosen_spans),
    )
    return events


def _select_run(
    spans: list[SpanData],
    roots: list[SpanData],
    *,
    trial_id: str,
    run_id: str | None,
) -> tuple[str, list[SpanData]]:
    """Partition spans by run root and return (root_id, spans_in_run)."""
    by_root = _group_spans_by_root(spans, roots)

    root_ids = [r.span_id for r in roots]
    if run_id is not None:
        if run_id not in by_root:
            raise ValueError(
                f"run_id={run_id!r} not found in trace {trial_id!r}; "
                f"available roots: {root_ids}"
            )
        return run_id, by_root[run_id]

    if len(roots) == 1:
        only = roots[0].span_id
        return only, by_root[only]

    # Multi-run: auto-pick by (event-span count desc, end_time desc).
    root_by_id = {r.span_id: r for r in roots}

    def _score(rid: str) -> tuple[int, int]:
        run_spans = by_root[rid]
        event_count = sum(
            1 for s in run_spans if s.operation_name.startswith(_EVENT_OP_PREFIX)
        )
        root = root_by_id[rid]
        end_time = root.start_time + root.duration
        return event_count, end_time

    ranked = sorted(by_root.keys(), key=_score, reverse=True)
    chosen = ranked[0]
    logger.warning(
        "SLS: trial=%s has %d runs; auto-selected run=%s (events=%d); "
        "discarded runs=%s",
        trial_id,
        len(roots),
        chosen,
        _score(chosen)[0],
        [(rid, _score(rid)[0]) for rid in ranked[1:]],
    )
    return chosen, by_root[chosen]


def _group_spans_by_root(
    spans: list[SpanData], roots: list[SpanData]
) -> dict[str, list[SpanData]]:
    """BFS from each root through parent_span_id to assign a root_id per span.

    Spans that don't chain up to any root are logged and dropped.
    """
    children: dict[str | None, list[SpanData]] = defaultdict(list)
    for s in spans:
        children[s.parent_span_id].append(s)

    result: dict[str, list[SpanData]] = {}
    assigned: set[str] = set()
    for root in roots:
        collected: list[SpanData] = [root]
        assigned.add(root.span_id)
        queue: deque[str] = deque([root.span_id])
        while queue:
            parent_id = queue.popleft()
            for child in children.get(parent_id, []):
                if child.span_id in assigned:
                    continue
                assigned.add(child.span_id)
                collected.append(child)
                queue.append(child.span_id)
        result[root.span_id] = collected

    orphans = [s for s in spans if s.span_id not in assigned]
    if orphans:
        logger.warning(
            "SLS: dropped %d orphan span(s) with no ancestor trial.started "
            "(first span_ids=%s)",
            len(orphans),
            [s.span_id for s in orphans[:5]],
        )
    return result
