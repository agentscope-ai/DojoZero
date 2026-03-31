import pytest

from dojozero.arena_server._utils import (
    _extract_agent_actions,
    _extract_agent_actions_from_spans,
)
from dojozero.core import SpanData


def _make_response_span(
    trial_id: str,
    span_id: str,
    timestamp: int,
    agent_id: str,
) -> SpanData:
    return SpanData(
        trace_id=trial_id,
        span_id=span_id,
        operation_name="agent.response",
        start_time=timestamp,
        duration=1,
        tags={
            "agent_id": agent_id,
            "content": f"{agent_id} response",
        },
    )


def test_extract_agent_actions_prefers_most_recent_trials_when_max_trials_limited():
    """Even with unordered trial_ids, extraction should prioritize freshest trial activity."""
    spans_by_trial = {
        "trial-old": [
            _make_response_span("trial-old", "old-1", 1000, "old-agent"),
            _make_response_span("trial-old", "old-2", 1010, "old-agent"),
        ],
        "trial-new": [
            _make_response_span("trial-new", "new-1", 5000, "new-agent"),
            _make_response_span("trial-new", "new-2", 5010, "new-agent"),
        ],
    }

    # Intentionally put stale trial first to reproduce unstable ordering behavior.
    unordered_trial_ids = ["trial-old", "trial-new"]
    actions = _extract_agent_actions_from_spans(
        spans_by_trial=spans_by_trial,
        agent_info_cache={},
        trial_ids=unordered_trial_ids,
        limit=20,
        max_trials=1,
    )

    assert len(actions) == 2
    assert all(action.agent.agent_id == "new-agent" for action in actions)


class _FakeTraceReader:
    def __init__(self, spans_by_trial: dict[str, list[SpanData]]) -> None:
        self._spans_by_trial = spans_by_trial

    async def get_spans(
        self,
        trial_id: str,
        start_time=None,
        operation_names=None,
    ) -> list[SpanData]:
        return self._spans_by_trial.get(trial_id, [])

    async def list_trials(
        self,
        start_time=None,
        end_time=None,
        limit: int = 500,
    ) -> list[str]:
        return list(self._spans_by_trial.keys())[:limit]

    async def get_all_spans(
        self,
        start_time=None,
        end_time=None,
        operation_names=None,
        max_concurrency: int = 10,
    ) -> list[SpanData]:
        all_spans: list[SpanData] = []
        for spans in self._spans_by_trial.values():
            all_spans.extend(spans)
        return all_spans

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_extract_agent_actions_on_demand_does_not_drop_recent_trial_before_sorting():
    spans_by_trial = {
        "trial-old": [
            _make_response_span("trial-old", "old-1", 1000, "old-agent"),
            _make_response_span("trial-old", "old-2", 1010, "old-agent"),
        ],
        "trial-new": [
            _make_response_span("trial-new", "new-1", 5000, "new-agent"),
            _make_response_span("trial-new", "new-2", 5010, "new-agent"),
        ],
    }
    reader = _FakeTraceReader(spans_by_trial)
    unordered_trial_ids = ["trial-old", "trial-new"]

    actions = await _extract_agent_actions(
        trace_reader=reader,
        trial_ids=unordered_trial_ids,
        cache=None,
        limit=20,
        max_trials=1,
    )

    assert len(actions) == 2
    assert all(action.agent.agent_id == "new-agent" for action in actions)
