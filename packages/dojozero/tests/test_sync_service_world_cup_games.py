from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from dojozero.arena_server._cache import LandingPageCache
from dojozero.core._tracing import SpanData
from dojozero.sync_service._sync import SyncService


TRIAL_ID = "world_cup-game-760455-66d30103"
TRACE_ID = "sls-jaeger-trace-id"


def _span(
    operation_name: str,
    *,
    start_time: int,
    tags: dict[str, Any] | None = None,
    trace_id: str = TRACE_ID,
) -> SpanData:
    return SpanData(
        trace_id=trace_id,
        span_id=f"span-{start_time}",
        operation_name=operation_name,
        start_time=start_time,
        duration=0,
        tags={
            "dojozero.trial.id": TRIAL_ID,
            **(tags or {}),
        },
    )


def _trial_started_span() -> SpanData:
    return _span(
        "trial.started",
        start_time=1,
        tags={
            "trial.home_tricode": "JOR",
            "trial.away_tricode": "ALG",
            "trial.home_team_name": "Jordan",
            "trial.away_team_name": "Algeria",
            "trial.game_date": "2026-06-22",
            "trial.sport_type": "world_cup",
            "trial.espn_game_id": "760455",
        },
    )


def _world_cup_update_span() -> SpanData:
    return _span(
        "event.world_cup_game_update",
        start_time=2,
        tags={
            "event.game_id": "760455",
            "event.sport": "world_cup",
            "event.period": 2,
            "event.game_clock": "90'+8'",
            "event.home_score": 1,
            "event.away_score": 2,
        },
    )


def _trial_stopped_span() -> SpanData:
    return _span(
        "trial.stopped",
        start_time=3,
        tags={"dojozero.trial.phase": "stopped"},
    )


class _TraceReader:
    def __init__(self, spans: list[SpanData]) -> None:
        self._spans = spans

    async def list_trials(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 500,
    ) -> list[str]:
        return [TRIAL_ID]

    async def get_all_spans(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        operation_names: list[str] | None = None,
        max_concurrency: int = 10,
    ) -> list[SpanData]:
        return self._spans

    async def get_spans(
        self,
        trial_id: str,
        start_time: datetime | None = None,
        operation_names: list[str] | None = None,
    ) -> list[SpanData]:
        return []

    async def close(self) -> None:
        return None


class _NoopRedisSyncService(SyncService):
    async def _write_to_redis(self, trial_ids: list[str], sync_time: datetime) -> None:
        return None


@pytest.mark.asyncio
async def test_sync_once_buckets_sls_spans_by_dojozero_trial_id() -> None:
    service = _NoopRedisSyncService(
        trace_reader=_TraceReader(
            [_trial_started_span(), _world_cup_update_span(), _trial_stopped_span()]
        ),
        redis_client=object(),  # type: ignore[arg-type]
    )

    await service._sync_once(is_initial=True)

    assert TRIAL_ID in service._spans_by_trial
    games = service._temp_cache.get_games() if service._temp_cache else None
    assert games is not None
    assert [(g.home_score, g.away_score) for g in games.completed_games] == [(1, 2)]


@pytest.mark.asyncio
async def test_refresh_aggregated_data_overlays_world_cup_scores_from_spans() -> None:
    service = _NoopRedisSyncService(
        trace_reader=_TraceReader([]),
        redis_client=object(),  # type: ignore[arg-type]
    )
    service._temp_cache = LandingPageCache()
    service._temp_cache.set_trial_info(
        TRIAL_ID,
        {
            "phase": "stopped",
            "metadata": {
                "home_team_tricode": "JOR",
                "away_team_tricode": "ALG",
                "home_team_name": "Jordan",
                "away_team_name": "Algeria",
                "game_date": "2026-06-22",
                "sport_type": "world_cup",
                "espn_game_id": "760455",
                "home_score": 0,
                "away_score": 0,
            },
        },
    )
    service._spans_by_trial = {TRIAL_ID: [_world_cup_update_span()]}

    await service._refresh_aggregated_data([TRIAL_ID])

    games = service._temp_cache.get_games()
    assert games is not None
    assert [(g.home_score, g.away_score) for g in games.completed_games] == [(1, 2)]
