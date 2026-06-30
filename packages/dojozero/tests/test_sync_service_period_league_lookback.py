"""Regression test: period per-league leaderboards honor the league lookback.

The all-time per-league board filters trials through `league_trial_ids` (league
match + per-league `league_lookback_days` cutoff). The period (7d/14d/30d) board
must use the same scope, otherwise a trial that the league lookback excludes can
still appear on the period board — inconsistent ranking between the two views.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from dojozero.arena_server._cache import CacheConfig, LandingPageCache
from dojozero.core._tracing import SpanData
from dojozero.sync_service._sync import SyncService


def _betting_final_stats_span(trial_id: str, agent_id: str) -> SpanData:
    return SpanData(
        trace_id=trial_id,
        span_id=f"{trial_id}-final",
        operation_name="broker.final_stats",
        start_time=1_000_000,
        duration=0,
        tags={
            "dojozero.trial.id": trial_id,
            "broker.contest_kind": "classic_betting",
            "broker.statistics": json.dumps(
                {
                    agent_id: {
                        "total_bets": 5,
                        "total_wagered": "100.00",
                        "wins": 3,
                        "losses": 2,
                        "win_rate": 60.0,
                        "net_profit": "50.00",
                        "roi": 0.5,
                    }
                }
            ),
        },
    )


class _TraceReader:
    async def get_spans(
        self,
        trial_id: str,
        start_time: datetime | None = None,
        operation_names: list[str] | None = None,
    ) -> list[SpanData]:
        return []

    async def close(self) -> None:
        return None


class _NoopRedisClient:
    async def get_schedules(self) -> list[dict[str, object]]:
        return []


def _trial_info(game_date: str) -> dict[str, Any]:
    return {
        "phase": "stopped",
        "metadata": {
            "sport_type": "nba",
            "game_date": game_date,
            "home_team_name": "Home",
            "away_team_name": "Away",
        },
    }


@pytest.mark.asyncio
async def test_period_board_applies_per_league_lookback_cutoff() -> None:
    now = datetime.now(timezone.utc)
    recent_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")  # within 5d lookback
    old_date = (now - timedelta(days=6)).strftime("%Y-%m-%d")  # outside 5d lookback

    service = SyncService(
        trace_reader=_TraceReader(),  # type: ignore[arg-type]
        redis_client=_NoopRedisClient(),  # type: ignore[arg-type]
        config=CacheConfig(
            league_lookback_days={"NBA": 5},
            trials_lookback_days=365,
        ),
    )
    service._temp_cache = LandingPageCache()
    service._temp_cache.set_trial_info("nba-recent", _trial_info(recent_date))
    service._temp_cache.set_trial_info("nba-old", _trial_info(old_date))
    service._spans_by_trial = {
        "nba-recent": [_betting_final_stats_span("nba-recent", "agent-recent")],
        "nba-old": [_betting_final_stats_span("nba-old", "agent-old")],
    }

    await service._refresh_aggregated_data(["nba-recent", "nba-old"])

    all_time = service._temp_cache.get_leaderboard(league="NBA") or []
    period_7d = service._temp_cache.get_leaderboard(league="NBA", period="7d") or []

    all_ids = {e.agent.agent_id for e in all_time}
    period_ids = {e.agent.agent_id for e in period_7d}

    # The all-time NBA board drops the 6-day-old trial (lookback = 5 days).
    assert all_ids == {"agent-recent"}
    # The 7d board must apply the same league scope: the old trial — though
    # within the 7-day window — is outside the NBA lookback, so it stays out.
    assert "agent-recent" in period_ids
    assert "agent-old" not in period_ids
