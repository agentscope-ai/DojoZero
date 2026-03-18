from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

from dojozero.arena_server._cache import (
    PeriodInfo,
    ReplayCache,
    ReplayCacheEntry,
    ReplayMetaInfo,
    LandingPageCache,
)
from dojozero.arena_server._constants import _get_team_identity
from dojozero.arena_server._models import (
    BetSummary,
    GameCardData,
    GamesResponse,
    ReplayErrorReason,
    StatsResponse,
)
from dojozero.betting import AgentResponseMessage, AgentInfo, BrokerFinalStats
from dojozero.core import (
    AgentAction,
    deserialize_span,
    LeaderboardEntry,
    serialize_span_for_ws,
    SpanData,
    TraceReader,
    TrialLifecycleSpan,
)
from dojozero.data import BaseGameUpdateEvent, GameInitializeEvent, TeamIdentity

LOGGER = logging.getLogger("dojozero.arena_server.utils")

# Operation names used for trial info extraction
TRIAL_INFO_OPERATION_NAMES = [
    "trial.started",
    "trial.stopped",
    "trial.terminated",
    "event.game_initialize",
    "event.game_result",
    "event.nba_game_update",
    "event.nfl_game_update",
]


async def _extract_trial_info_from_traces(
    trace_reader: TraceReader,
    trial_id: str,
) -> dict[str, Any]:
    """Extract trial phase and metadata from trace spans.

    Fetches relevant spans then delegates to _extract_trial_info_from_spans.
    """
    try:
        spans = await trace_reader.get_spans(
            trial_id,
            operation_names=TRIAL_INFO_OPERATION_NAMES,
        )
    except Exception as e:
        LOGGER.warning("Failed to get spans for trial '%s': %s", trial_id, e)
        return {"phase": "unknown", "metadata": {}}
    return _extract_trial_info_from_spans(spans)


def _extract_trial_info_from_spans(spans: list[SpanData]) -> dict[str, Any]:
    """Extract trial phase and metadata from pre-fetched spans.

    This is the core processing logic, separated from fetching for batch operations.

    Args:
        spans: Pre-fetched spans for a single trial

    Returns:
        dict with "phase", "metadata", and optional "game_init"
    """
    has_started = False
    has_stopped = False
    has_game_result = False
    latest_start_time = 0
    latest_stop_time = 0

    metadata: dict[str, Any] = {}
    game_init: GameInitializeEvent | None = None
    latest_game_update: BaseGameUpdateEvent | None = None
    latest_game_update_time = 0

    for span in spans:
        typed = deserialize_span(span)

        if isinstance(typed, TrialLifecycleSpan):
            if typed.phase == "started":
                has_started = True
                if typed.start_time > latest_start_time:
                    latest_start_time = typed.start_time
                metadata.update(
                    {
                        "home_team_tricode": typed.home_team_tricode,
                        "away_team_tricode": typed.away_team_tricode,
                        "home_team_name": typed.home_team_name,
                        "away_team_name": typed.away_team_name,
                        "game_date": typed.game_date,
                        "sport_type": typed.sport_type,
                        "espn_game_id": typed.espn_game_id,
                        **typed.extra_metadata,
                    }
                )
            elif typed.phase in ("stopped", "terminated"):
                has_stopped = True
                if typed.start_time > latest_stop_time:
                    latest_stop_time = typed.start_time

        elif isinstance(typed, GameInitializeEvent):
            game_init = typed

        elif isinstance(typed, BaseGameUpdateEvent):
            span_time = span.start_time
            if span_time > latest_game_update_time:
                latest_game_update_time = span_time
                latest_game_update = typed

        elif "game_result" in span.operation_name:
            has_game_result = True

    # Add live scores to metadata if we have a game update
    if latest_game_update is not None:
        metadata["home_score"] = latest_game_update.home_score
        metadata["away_score"] = latest_game_update.away_score
        metadata["period"] = latest_game_update.period
        metadata["game_clock"] = latest_game_update.game_clock

    # Determine phase
    if has_stopped and latest_stop_time >= latest_start_time:
        phase = "stopped"
    elif has_game_result:
        phase = "completed"
    elif has_started and not has_stopped:
        phase = "running"
    elif has_stopped:
        phase = "stopped"
    elif spans:
        phase = "running"
    else:
        phase = "unknown"

    return {"phase": phase, "metadata": metadata, "game_init": game_init}


async def _filter_trials_by_league(
    trace_reader: TraceReader,
    trial_ids: list[str],
    league: str | None,
    cache: "LandingPageCache | None" = None,
) -> list[str]:
    """Filter trial IDs by league/sport type.

    This is a pure filtering function that takes an existing list of trial IDs
    and returns only those matching the specified league.

    Args:
        trace_reader: TraceReader for fetching trial info
        trial_ids: List of trial IDs to filter
        league: League to filter by (e.g., 'NBA', 'NFL'). None means return all.
        cache: Optional cache for trial info (uses cache if available, fetches if not)

    Returns:
        Filtered list of trial IDs matching the specified league
    """
    if league is None:
        return trial_ids

    league_upper = league.upper()
    filtered: list[str] = []

    for trial_id in trial_ids:
        try:
            # Try cache first, then fetch if not cached
            trial_info = None
            if cache is not None:
                trial_info = cache.get_trial_info(trial_id)

            if trial_info is None:
                trial_info = await _extract_trial_info_from_traces(
                    trace_reader, trial_id
                )
                # Store in cache if available
                if cache is not None:
                    cache.set_trial_info(trial_id, trial_info)

            metadata = trial_info.get("metadata", {})
            # Note: BettingTrialMetadata has no `league` field.
            # `sport_type` is used instead and is treated as the league.
            trial_league = metadata.get("sport_type", "")

            if trial_league.upper() == league_upper:
                filtered.append(trial_id)

        except Exception as e:
            LOGGER.warning(
                "Failed to get info for trial '%s' during filtering: %s",
                trial_id,
                e,
            )
            continue

    LOGGER.debug(
        "Filtered trials by league '%s': %d/%d matched",
        league,
        len(filtered),
        len(trial_ids),
    )
    return filtered


def _resolve_team_identity(
    team: TeamIdentity | str,
    fallback_tricode: str,
    fallback_name: str,
    league: str,
) -> TeamIdentity:
    """Resolve a team to a TeamIdentity, applying fallbacks as needed."""
    if isinstance(team, TeamIdentity) and team:
        # Ensure tricode is populated
        if not team.tricode and fallback_tricode:
            return team.model_copy(update={"tricode": fallback_tricode})
        return team
    # Fallback to static lookup, then override name if provided
    identity = _get_team_identity(fallback_tricode, league)
    if fallback_name and fallback_name != identity.name:
        return identity.model_copy(update={"name": fallback_name})
    return identity


def _parse_bet_selection(selection: str) -> tuple[str, str]:
    """Parse bet selection string to extract team and type.

    Args:
        selection: Selection string from BetExecutedPayload

    Returns:
        Tuple of (team, bet_type)

    Examples:
        "LAL_ML" -> ("LAL", "moneyline")
        "LAL_SPREAD_-3.5" -> ("LAL", "spread")
        "OVER_220.5" -> ("OVER", "total")
    """
    parts = selection.split("_")
    if len(parts) == 2 and parts[1] == "ML":
        return parts[0], "moneyline"
    elif len(parts) >= 2 and parts[1] == "SPREAD":
        return parts[0], "spread"
    elif parts[0] in ("OVER", "UNDER"):
        return parts[0], "total"
    else:
        # Default fallback
        return parts[0] if parts else selection, "moneyline"


async def _extract_bets_for_trial(
    trace_reader: TraceReader,
    trial_id: str,
    cache: "LandingPageCache | None" = None,
    limit: int = 10,
) -> list["BetSummary"]:
    """Extract recent bets from broker.bet spans for a specific trial.

    Args:
        trace_reader: TraceReader for querying the trace store
        trial_id: Trial ID to query
        cache: Optional cache for agent info lookup
        limit: Maximum number of bets to return

    Returns:
        List of recent bets formatted as BetSummary
    """
    from dojozero.arena_server._models import BetSummary
    from dojozero.betting import BetExecutedPayload

    try:
        # Query broker.bet spans
        spans = await trace_reader.get_spans(
            trial_id,
            operation_names=["broker.bet"],
        )
    except Exception as e:
        LOGGER.warning("Failed to get broker.bet spans for trial '%s': %s", trial_id, e)
        return []

    bets: list[BetSummary] = []
    for span in spans:
        typed = deserialize_span(span)
        if not isinstance(typed, BetExecutedPayload):
            continue

        # Get agent info from cache
        agent_info = cache.get_agent_info(typed.agent_id) if cache else None
        if agent_info is None:
            # Fallback: create minimal AgentInfo
            agent_info = AgentInfo(agent_id=typed.agent_id, persona=typed.agent_id)

        # Parse selection to extract team and type
        team, bet_type = _parse_bet_selection(typed.selection)

        try:
            amount = float(typed.amount)
        except (ValueError, TypeError):
            amount = 0.0

        bets.append(
            BetSummary(
                agent=agent_info,
                team=team,
                amount=amount,
                type=bet_type,
            )
        )

    # Return most recent bets (limited)
    return bets[-limit:] if bets else []


async def _extract_games_from_trials(
    trace_reader: TraceReader,
    trial_ids: list[str],
    cache: "LandingPageCache | None" = None,
) -> GamesResponse:
    """Extract games list from trials for landing page.

    Args:
        trace_reader: Trace reader for fetching spans
        trial_ids: List of trial IDs to process
        cache: Optional cache for trial info (uses cache if available, fetches if not)
    """
    live_games: list[GameCardData] = []
    completed_games: list[GameCardData] = []

    for trial_id in trial_ids:
        try:
            # Try cache first, then fetch if not cached
            trial_info = None
            if cache is not None:
                trial_info = cache.get_trial_info(trial_id)

            if trial_info is None:
                trial_info = await _extract_trial_info_from_traces(
                    trace_reader, trial_id
                )
                # Store in cache if available
                if cache is not None:
                    cache.set_trial_info(trial_id, trial_info)
        except Exception as e:
            LOGGER.warning("Failed to get info for trial '%s': %s", trial_id, e)
            continue

        phase = trial_info["phase"]
        metadata = trial_info["metadata"]
        # Normalize league to uppercase for frontend compatibility
        league = metadata.get("sport_type", "NBA").upper()

        home_tricode = metadata.get("home_team_tricode", "TBD")
        away_tricode = metadata.get("away_team_tricode", "TBD")

        # Prefer rich team data from GameInitializeEvent (full TeamIdentity)
        game_init = trial_info.get("game_init")
        if isinstance(game_init, GameInitializeEvent):
            home_team = _resolve_team_identity(
                game_init.home_team, home_tricode, "", league
            )
            away_team = _resolve_team_identity(
                game_init.away_team, away_tricode, "", league
            )
        else:
            home_team = _resolve_team_identity(
                "", home_tricode, metadata.get("home_team_name", ""), league
            )
            away_team = _resolve_team_identity(
                "", away_tricode, metadata.get("away_team_name", ""), league
            )

        # Fetch bets for live games only (performance optimization)
        bets = []
        if phase == "running":
            try:
                bets = await _extract_bets_for_trial(
                    trace_reader, trial_id, cache, limit=10
                )
            except Exception as e:
                LOGGER.warning("Failed to get bets for trial '%s': %s", trial_id, e)

        # Extract game timestamp from GameInitializeEvent (ISO format with seconds)
        # Fallback to metadata game_date if GameInitializeEvent not available
        game_date_str = metadata.get("game_date", "")
        if isinstance(game_init, GameInitializeEvent):
            # Prefer game_timestamp (actual game time), fallback to game_time
            event_time = game_init.game_timestamp or game_init.game_time
            if event_time:
                game_date_str = event_time.isoformat()

        # Map phase to frontend status
        status = (
            "live"
            if phase == "running"
            else "completed"
            if phase in ("completed", "stopped")
            else phase
        )

        game_card = GameCardData(
            id=trial_id,
            league=league,
            home_team=home_team,
            away_team=away_team,
            home_score=metadata.get("home_score", 0),
            away_score=metadata.get("away_score", 0),
            status=status,
            date=game_date_str,
            quarter=metadata.get("quarter", "") if phase == "running" else "",
            clock=metadata.get("clock", "") if phase == "running" else "",
            bets=bets,
            winner=metadata.get("winner_agent")
            if phase in ("completed", "stopped")
            else None,
            win_amount=metadata.get("win_amount", 0)
            if phase in ("completed", "stopped")
            else 0,
        )

        if phase == "running":
            live_games.append(game_card)
        elif phase in ("completed", "stopped"):
            completed_games.append(game_card)

    return GamesResponse(
        live_games=live_games,
        completed_games=completed_games,
    )


def _extract_agent_actions_from_spans(
    spans_by_trial: dict[str, list[SpanData]],
    agent_info_cache: dict[str, AgentInfo],
    trial_ids: list[str] | None = None,
    limit: int = 20,
    max_trials: int = 5,
) -> list[AgentAction]:
    """Extract recent agent actions from pre-fetched spans.

    Args:
        spans_by_trial: Pre-fetched spans grouped by trial_id
        agent_info_cache: Pre-populated agent info cache (agent_id -> AgentInfo)
        trial_ids: Optional list to filter which trials to process (None = all)
        limit: Maximum number of actions to return
        max_trials: Maximum number of trials to process

    Returns:
        List of agent actions sorted by time (newest first)
    """
    all_actions: list[AgentAction] = []

    def _latest_span_time(spans: list[SpanData]) -> int:
        return max((span.start_time for span in spans), default=0)

    # Filter to requested trials or use all
    trials_to_process = (
        trial_ids if trial_ids is not None else list(spans_by_trial.keys())
    )
    # Process most recently active trials first so max_trials does not bias toward
    # stale trial_ids ordering from upstream trace backends.
    sorted_trials = sorted(
        trials_to_process,
        key=lambda trial_id: _latest_span_time(spans_by_trial.get(trial_id, [])),
        reverse=True,
    )

    LOGGER.debug(
        "Extracting agent actions from %d trials (limit=%d, max_trials=%d)",
        min(len(sorted_trials), max_trials),
        limit,
        max_trials,
    )

    for trial_id in sorted_trials[:max_trials]:
        spans = spans_by_trial.get(trial_id, [])

        # Filter to agent.response spans
        response_spans = [s for s in spans if s.operation_name == "agent.response"]
        LOGGER.debug(
            "Trial %s: found %d agent.response spans", trial_id, len(response_spans)
        )

        for span in response_spans:
            typed = deserialize_span(span)
            if not isinstance(typed, AgentResponseMessage):
                continue

            agent_id = typed.agent_id
            if not agent_id:
                continue

            # Get agent info from pre-populated cache
            agent_info = agent_info_cache.get(agent_id)
            if agent_info is None:
                # Fallback: create minimal AgentInfo
                agent_info = AgentInfo(agent_id=agent_id, persona=agent_id)

            all_actions.append(
                AgentAction(
                    agent=agent_info,
                    response=typed,
                    timestamp=span.start_time,
                )
            )

        # Early exit if we have enough actions (optimization)
        if len(all_actions) >= limit * 2:
            LOGGER.debug("Early exit: collected %d actions", len(all_actions))
            break

    # Sort by timestamp (newest first) and limit
    all_actions.sort(key=lambda x: x.timestamp, reverse=True)
    result = all_actions[:limit]
    LOGGER.debug(
        "Returning %d actions (from %d total)",
        len(result),
        len(all_actions),
    )
    return result


async def _extract_agent_actions(
    trace_reader: TraceReader,
    trial_ids: list[str],
    cache: "LandingPageCache | None" = None,
    limit: int = 20,
    max_trials: int = 5,
) -> list[AgentAction]:
    """Extract recent agent actions (on-demand version).

    Fetches spans then delegates to _extract_agent_actions_from_spans.
    """
    spans_by_trial: dict[str, list[SpanData]] = {}
    for trial_id in trial_ids:
        try:
            spans = await trace_reader.get_spans(
                trial_id,
                operation_names=["agent.response"],
            )
            spans_by_trial[trial_id] = spans
        except Exception as e:
            LOGGER.warning("Failed to get spans for trial '%s': %s", trial_id, e)

    agent_info_cache = cache.get_all_agent_info() if cache else {}
    return _extract_agent_actions_from_spans(
        spans_by_trial,
        agent_info_cache,
        trial_ids,
        limit=limit,
        max_trials=max_trials,
    )


async def _compute_stats(
    trace_reader: TraceReader,
    trial_ids: list[str],
    cache: "LandingPageCache | None" = None,
    spans_by_trial: dict[str, list[SpanData]] | None = None,
) -> StatsResponse:
    """Compute aggregate stats for landing page.

    Args:
        trace_reader: Trace reader for fetching spans
        trial_ids: List of trial IDs to process
        cache: Optional cache for trial info and agent info
        spans_by_trial: Optional pre-fetched spans grouped by trial_id for wagered calculation
    """
    games_played = 0
    live_now = 0
    wagered_today = 0.0

    for trial_id in trial_ids:
        try:
            # Try cache first, then fetch if not cached
            trial_info = None
            if cache is not None:
                trial_info = cache.get_trial_info(trial_id)

            if trial_info is None:
                trial_info = await _extract_trial_info_from_traces(
                    trace_reader, trial_id
                )
                # Store in cache if available
                if cache is not None:
                    cache.set_trial_info(trial_id, trial_info)
        except Exception:
            continue

        phase = trial_info["phase"]

        # Count completed games (both "completed" and "stopped" phases)
        if phase in ("completed", "stopped"):
            games_played += 1
        elif phase == "running":
            live_now += 1

    # Calculate total wagered from broker.final_stats spans
    wagered_today = await _compute_total_wagered(
        trace_reader, trial_ids, spans_by_trial
    )

    # Calculate total bet counts from broker.final_stats spans
    bet_counts = await _compute_total_bet_counts(
        trace_reader, trial_ids, spans_by_trial
    )

    # Get total agents from cache
    total_agents = cache.get_total_agents() if cache else 0

    return StatsResponse(
        games_played=games_played,
        live_now=live_now,
        wagered_today=int(wagered_today),
        total_agents=total_agents,
        bet_counts=bet_counts,
    )


async def _compute_total_wagered(
    trace_reader: TraceReader,
    trial_ids: list[str],
    spans_by_trial: dict[str, list[SpanData]] | None = None,
) -> float:
    """Compute total wagered amount from broker.final_stats spans.

    Args:
        trace_reader: Trace reader for fetching spans
        trial_ids: List of trial IDs to process
        spans_by_trial: Optional pre-fetched spans grouped by trial_id

    Returns:
        Total wagered amount across all trials
    """
    total_wagered = 0.0

    for trial_id in trial_ids:
        try:
            # Use pre-fetched spans if available, otherwise query
            if spans_by_trial is not None:
                spans = spans_by_trial.get(trial_id, [])
                # Filter to broker.final_stats spans
                final_stats_spans = [
                    s for s in spans if s.operation_name == "broker.final_stats"
                ]
            else:
                final_stats_spans = await trace_reader.get_spans(
                    trial_id,
                    operation_names=["broker.final_stats"],
                )

            # Extract total_wagered from StatisticsList
            for span in final_stats_spans:
                typed = deserialize_span(span)
                if isinstance(typed, BrokerFinalStats):
                    for stats in typed.statistics.values():
                        total_wagered += float(stats.total_wagered)

        except Exception as e:
            LOGGER.warning(
                "Failed to get broker.final_stats for trial '%s': %s",
                trial_id,
                e,
            )
            continue

    return total_wagered


async def _compute_total_bet_counts(
    trace_reader: TraceReader,
    trial_ids: list[str],
    spans_by_trial: dict[str, list[SpanData]] | None = None,
) -> int:
    """Compute total bet counts from broker.final_stats spans.

    Args:
        trace_reader: Trace reader for fetching spans
        trial_ids: List of trial IDs to process
        spans_by_trial: Optional pre-fetched spans grouped by trial_id

    Returns:
        Total bet counts across all trials
    """
    total_bet_counts = 0

    for trial_id in trial_ids:
        try:
            # Use pre-fetched spans if available, otherwise query
            if spans_by_trial is not None:
                spans = spans_by_trial.get(trial_id, [])
                # Filter to broker.final_stats spans
                final_stats_spans = [
                    s for s in spans if s.operation_name == "broker.final_stats"
                ]
            else:
                final_stats_spans = await trace_reader.get_spans(
                    trial_id,
                    operation_names=["broker.final_stats"],
                )

            # Extract bets_count from BrokerFinalStats
            for span in final_stats_spans:
                typed = deserialize_span(span)
                if isinstance(typed, BrokerFinalStats):
                    total_bet_counts += typed.bets_count

        except Exception as e:
            LOGGER.warning(
                "Failed to get broker.final_stats for trial '%s': %s",
                trial_id,
                e,
            )
            continue

    return total_bet_counts


def _compute_leaderboard_from_spans(
    spans_by_trial: dict[str, list[SpanData]],
    agent_info_cache: dict[str, AgentInfo],
    trial_ids: list[str] | None = None,
    limit: int = 20,
) -> list[LeaderboardEntry]:
    """Compute agent leaderboard from pre-fetched spans.

    Uses broker.final_stats spans when available for accurate statistics.
    Falls back to counting agent.response spans if final_stats not found.

    Args:
        spans_by_trial: Pre-fetched spans grouped by trial_id
        agent_info_cache: Pre-populated agent info cache (agent_id -> AgentInfo)
        trial_ids: Optional list to filter which trials to process (None = all)
        limit: Maximum entries to return

    Returns:
        List of agents sorted by winnings (highest first)
    """
    from dojozero.betting import StatisticsList

    # Accumulator for per-agent stats
    @dataclass
    class _AgentStats:
        agent: AgentInfo
        winnings: float = 0.0
        wins: int = 0
        total_bets: int = 0
        total_wagered: float = 0.0

    agent_stats: dict[str, _AgentStats] = {}

    # Filter to requested trials or use all
    trials_to_process = (
        trial_ids if trial_ids is not None else list(spans_by_trial.keys())
    )

    for trial_id in trials_to_process:
        spans = spans_by_trial.get(trial_id, [])
        if not spans:
            continue

        # Separate spans by operation
        final_stats_spans = [
            s for s in spans if s.operation_name == "broker.final_stats"
        ]
        response_spans = [s for s in spans if s.operation_name == "agent.response"]

        if final_stats_spans:
            # Use final_stats if available
            for span in final_stats_spans:
                typed = deserialize_span(span)
                if isinstance(typed, StatisticsList):
                    for agent_id, stats in typed.statistics.items():
                        agent_info = agent_info_cache.get(agent_id)
                        if agent_info is None:
                            agent_info = AgentInfo(agent_id=agent_id, persona=agent_id)

                        if agent_id not in agent_stats:
                            agent_stats[agent_id] = _AgentStats(agent=agent_info)

                        acc = agent_stats[agent_id]
                        acc.winnings += float(stats.net_profit)
                        acc.wins += stats.wins
                        acc.total_bets += stats.total_bets
                        acc.total_wagered += float(stats.total_wagered)
        else:
            # Fallback: count from agent.response spans
            for span in response_spans:
                typed = deserialize_span(span)
                if not isinstance(typed, AgentResponseMessage):
                    continue

                agent_id = typed.agent_id
                if not agent_id:
                    continue

                if agent_id not in agent_stats:
                    agent_info = agent_info_cache.get(agent_id)
                    if agent_info is None:
                        agent_info = AgentInfo(agent_id=agent_id, persona=agent_id)
                    agent_stats[agent_id] = _AgentStats(agent=agent_info)

                acc = agent_stats[agent_id]
                if typed.bet_amount:
                    acc.total_bets += 1
                    acc.total_wagered += typed.bet_amount

    # Convert to sorted list
    leaderboard: list[LeaderboardEntry] = []
    for stats in agent_stats.values():
        win_rate = (stats.wins / stats.total_bets * 100) if stats.total_bets > 0 else 0
        roi = (
            (stats.winnings / stats.total_wagered * 100)
            if stats.total_wagered > 0
            else 0
        )

        leaderboard.append(
            LeaderboardEntry(
                agent=stats.agent,
                winnings=round(stats.winnings, 2),
                winRate=round(win_rate, 1),
                totalBets=stats.total_bets,
                roi=round(roi, 1),
            )
        )

    # Sort by winnings (descending) and add rank
    leaderboard.sort(key=lambda x: x.winnings, reverse=True)
    ranked = [
        entry.model_copy(update={"rank": i + 1})
        for i, entry in enumerate(leaderboard[:limit])
    ]

    return ranked


async def _compute_leaderboard(
    trace_reader: TraceReader,
    trial_ids: list[str],
    cache: "LandingPageCache | None" = None,
    limit: int = 20,
) -> list[LeaderboardEntry]:
    """Compute agent leaderboard (on-demand version).

    Fetches spans then delegates to _compute_leaderboard_from_spans.
    """
    spans_by_trial: dict[str, list[SpanData]] = {}
    for trial_id in trial_ids:
        try:
            spans = await trace_reader.get_spans(
                trial_id,
                operation_names=["broker.final_stats", "agent.response"],
            )
            spans_by_trial[trial_id] = spans
        except Exception as e:
            LOGGER.warning(
                "Failed to get spans for leaderboard from trial '%s': %s",
                trial_id,
                e,
            )

    agent_info_cache = cache.get_all_agent_info() if cache else {}
    return _compute_leaderboard_from_spans(
        spans_by_trial,
        agent_info_cache,
        trial_ids,
        limit=limit,
    )


def _compute_replay_meta(
    items: list[dict[str, Any]],
    core_categories: list[str],
) -> ReplayMetaInfo:
    """Compute replay metadata from serialized items.

    Scans through items once to build:
    - play_item_indices: mapping from play_index to item_index
    - periods: list of PeriodInfo with play counts per period

    Args:
        items: List of serialized span dicts with "category" and "data" keys
        core_categories: Categories to count as "plays" (e.g., ["play"])

    Returns:
        ReplayMetaInfo with pre-computed indices and period info
    """
    play_item_indices: list[int] = []
    period_play_counts: dict[int, int] = {}  # period -> play count
    period_start_indices: dict[int, int] = {}  # period -> first play index

    current_period: int = 1  # Default period

    for item_index, item in enumerate(items):
        category = item.get("category", "")
        data = item.get("data", {})

        # Track core category items (plays)
        if category in core_categories:
            play_index = len(play_item_indices)
            play_item_indices.append(item_index)

            # Get period from play data
            period = data.get("period")
            if period is not None and isinstance(period, int):
                current_period = period

            # Track period stats
            if current_period not in period_play_counts:
                period_play_counts[current_period] = 0
                period_start_indices[current_period] = play_index
            period_play_counts[current_period] += 1

    # Build sorted periods list
    periods: list[PeriodInfo] = []
    for period in sorted(period_play_counts.keys()):
        periods.append(
            PeriodInfo(
                period=period,
                play_count=period_play_counts[period],
                start_play_index=period_start_indices[period],
            )
        )

    return ReplayMetaInfo(
        total_play_count=len(play_item_indices),
        play_item_indices=play_item_indices,
        periods=periods,
    )


def _process_spans_for_replay(
    spans: list[SpanData],
) -> tuple[list[dict[str, Any]], bool]:
    """Process spans into replay items (CPU-bound, runs in thread pool)."""
    has_ended = False
    items: list[dict[str, Any]] = []

    for span in spans:
        typed = deserialize_span(span)
        if typed is None:
            continue
        items.append(serialize_span_for_ws(typed))
        if isinstance(typed, TrialLifecycleSpan) and typed.phase in (
            "stopped",
            "terminated",
        ):
            has_ended = True

    return items, has_ended


async def _load_replay_data(
    trace_reader: TraceReader,
    replay_cache: ReplayCache,
    trial_id: str,
) -> tuple[ReplayCacheEntry | None, ReplayErrorReason | Literal[""]]:
    """Load replay data for a trial from the trace store (Jaeger).

    Returns:
        Tuple of (cache_entry, error_reason)
        - If successful: (ReplayCacheEntry, "")
        - If failed: (None, reason)

    Reasons:
        - "trial_not_found": No spans found for trial
        - "trial_still_running": Trial hasn't ended yet
        - "no_data": Trial exists but no spans to replay
    """
    # 1. Check cache first
    cached = replay_cache.get(trial_id)
    if cached:
        return cached, ""

    # 2. Fetch spans from trace store
    try:
        spans = await trace_reader.get_spans(trial_id)
    except Exception as e:
        LOGGER.error("Failed to fetch spans for replay: %s", e)
        return None, "trial_not_found"

    if not spans:
        return None, "trial_not_found"

    # 3. Process spans in thread pool (CPU-bound)
    items, has_ended = await asyncio.to_thread(_process_spans_for_replay, spans)

    if not has_ended:
        LOGGER.info("Trial %s has not ended yet, replay unavailable", trial_id)
        return None, "trial_still_running"

    if not items:
        return None, "no_data"

    # 4. Compute metadata in thread pool and cache
    meta = await asyncio.to_thread(
        _compute_replay_meta, items, replay_cache.core_categories
    )
    replay_cache.set(trial_id, items, meta)

    return ReplayCacheEntry(items=items, meta=meta), ""
