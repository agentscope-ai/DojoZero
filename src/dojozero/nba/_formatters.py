"""Event formatters for NBA moneyline betting agent."""

import json
from typing import Any

from dojozero.data._models import (
    DataEvent,
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    OddsUpdateEvent,
)
from dojozero.data.espn._stats_events import PreGameStatsEvent
from dojozero.data.nba._events import NBAGameUpdateEvent, NBAPlayEvent
from dojozero.data.websearch._formatters import WEBSEARCH_EVENT_FORMATTERS
from dojozero.betting._models import BetExecutedPayload, BetSettledPayload


def _format_game_initialize(event: GameInitializeEvent) -> str:
    """Format GameInitializeEvent to readable text."""
    home_team = str(event.home_team)
    away_team = str(event.away_team)
    game_time = event.game_time

    time_str = f" at {game_time.strftime('%Y-%m-%d %H:%M UTC')}"

    return f"[Game Initialized] {away_team} @ {home_team}{time_str}"


def _format_game_start(event: GameStartEvent) -> str:
    """Format GameStartEvent to readable text."""
    return f"[Game Started] Game ID: {event.game_id}"


def _format_game_result(event: GameResultEvent) -> str:
    """Format GameResultEvent to readable text."""
    home_score = event.home_score
    away_score = event.away_score
    winner_str = (
        "Home Team"
        if event.winner == "home"
        else "Away Team"
        if event.winner == "away"
        else event.winner
    )

    return f"[Game Finished] {winner_str} wins! Final Score: Home {home_score} - Away {away_score}"


def _format_game_update(event: NBAGameUpdateEvent) -> str:
    """Format NBAGameUpdateEvent to readable text."""
    period = event.period
    game_clock = event.game_clock
    home = event.home_team_stats
    away = event.away_team_stats

    period_name = f"Q{period}" if period <= 4 else f"OT{period - 4}"
    clock_str = f" | {game_clock}" if game_clock else ""

    lines = [
        f"[Game Update] {period_name}{clock_str}",
        f"{away.team_name} ({away.team_tricode}): {away.score}",
        f"{home.team_name} ({home.team_tricode}): {home.score}",
    ]

    return "\n".join(lines)


def _format_odds_update(event: OddsUpdateEvent) -> str:
    """Format OddsUpdateEvent to readable text."""
    lines = ["[Odds Update]"]

    ml = event.odds.moneyline
    if ml:
        lines.append(
            f"- Home: {ml.home_odds:.2f} ({ml.home_probability * 100:.1f}% implied probability)"
        )
        lines.append(
            f"- Away: {ml.away_odds:.2f} ({ml.away_probability * 100:.1f}% implied probability)"
        )

    for sp in event.odds.spreads:
        lines.append(
            f"- Spread: {sp.spread:+.1f} (Home: {sp.home_odds:.2f}, Away: {sp.away_odds:.2f})"
        )

    for tot in event.odds.totals:
        lines.append(
            f"- Total: O/U {tot.total:.1f} (Over: {tot.over_odds:.2f}, Under: {tot.under_odds:.2f})"
        )

    return "\n".join(lines)


def _format_play_by_play(event: NBAPlayEvent) -> str:
    """Format NBAPlayEvent to readable text."""
    period = event.period
    clock = event.clock
    action_type = event.action_type
    player_name = event.player_name
    team_tricode = event.team_tricode
    description = event.description
    home_score = event.home_score
    away_score = event.away_score

    period_name = f"Q{period}" if period <= 4 else f"OT{period - 4}"
    player_str = f" [{player_name}]" if player_name else ""
    team_str = f" ({team_tricode})" if team_tricode else ""

    return f"[Play] {period_name} {clock} | {action_type.upper()}{team_str}{player_str}: {description} [Score: {away_score}-{home_score}]"


def _format_bet_executed(payload: BetExecutedPayload) -> str:
    """Format BetExecutedPayload to readable text."""
    return (
        f"[Bet Executed] Bet ID: {payload.bet_id}\n"
        f"- Event: {payload.event_id}\n"
        f"- Selection: {payload.selection}\n"
        f"- Amount: ${payload.amount}\n"
        f"- Odds: {payload.execution_odds}\n"
        f"- Time: {payload.execution_time}"
    )


def _format_bet_settled(payload: BetSettledPayload) -> str:
    """Format BetSettledPayload to readable text."""
    outcome_str = payload.outcome.value  # WIN or LOSS
    return (
        f"[Bet Settled] Bet ID: {payload.bet_id}\n"
        f"- Event: {payload.event_id}\n"
        f"- Outcome: {outcome_str}\n"
        f"- Payout: ${payload.payout}\n"
        f"- Winner: {payload.winner}"
    )


def _format_pregame_stats(event: PreGameStatsEvent) -> str:
    """Format PreGameStatsEvent to readable text."""
    lines = ["[Pre-Game Stats]"]

    # Season Series (Head-to-Head)
    ss = event.season_series
    if ss and ss.total_games > 0:
        leader = (
            "Home leads"
            if ss.home_wins > ss.away_wins
            else "Away leads"
            if ss.away_wins > ss.home_wins
            else "Tied"
        )
        lines.append(f"\n**Season Series**: {ss.home_wins}-{ss.away_wins} ({leader})")

    # Recent Form
    home_form = event.home_recent_form
    away_form = event.away_recent_form
    if home_form or away_form:
        lines.append("\n**Recent Form**")
        if home_form:
            streak_str = f", {home_form.streak}" if home_form.streak else ""
            lines.append(
                f"- Home ({home_form.team_name}): {home_form.wins}-{home_form.losses} L{home_form.last_n}{streak_str} | "
                f"{home_form.avg_points_scored:.1f} PPG, {home_form.avg_points_allowed:.1f} OPP"
            )
        if away_form:
            streak_str = f", {away_form.streak}" if away_form.streak else ""
            lines.append(
                f"- Away ({away_form.team_name}): {away_form.wins}-{away_form.losses} L{away_form.last_n}{streak_str} | "
                f"{away_form.avg_points_scored:.1f} PPG, {away_form.avg_points_allowed:.1f} OPP"
            )

    # Schedule & Rest
    home_sched = event.home_schedule
    away_sched = event.away_schedule
    if home_sched or away_sched:
        lines.append("\n**Rest & Schedule**")
        if home_sched:
            b2b_str = " (B2B)" if home_sched.is_back_to_back else ""
            lines.append(
                f"- Home: {home_sched.days_rest} days rest{b2b_str}, {home_sched.games_last_7_days} games last 7 days"
            )
        if away_sched:
            b2b_str = " (B2B)" if away_sched.is_back_to_back else ""
            lines.append(
                f"- Away: {away_sched.days_rest} days rest{b2b_str}, {away_sched.games_last_7_days} games last 7 days"
            )

    # Team Season Stats
    home_stats = event.home_team_stats
    away_stats = event.away_team_stats
    if home_stats or away_stats:
        lines.append("\n**Season Stats**")
        for label, stats in [("Home", home_stats), ("Away", away_stats)]:
            if stats and stats.stats:
                ppg = stats.stats.get("avgPointsPerGame", stats.stats.get("ppg", 0))
                opp_ppg = stats.stats.get(
                    "avgPointsAllowed", stats.stats.get("oppg", 0)
                )
                ppg_rank = stats.rank.get("avgPointsPerGame", stats.rank.get("ppg", 0))
                lines.append(
                    f"- {label} ({stats.team_name}): {ppg:.1f} PPG"
                    + (f" (#{ppg_rank})" if ppg_rank else "")
                    + (f", {opp_ppg:.1f} OPP" if opp_ppg else "")
                )

    # Home/Away Splits
    home_splits = event.home_splits
    away_splits = event.away_splits
    if home_splits or away_splits:
        lines.append("\n**Home/Away Splits**")
        if home_splits:
            lines.append(
                f"- Home ({home_splits.team_name}): {home_splits.home_record} at home, {home_splits.away_record} away"
            )
        if away_splits:
            lines.append(
                f"- Away ({away_splits.team_name}): {away_splits.home_record} at home, {away_splits.away_record} away"
            )

    # Standings
    home_stand = event.home_standings
    away_stand = event.away_standings
    if home_stand or away_stand:
        lines.append("\n**Standings**")
        for label, stand in [("Home", home_stand), ("Away", away_stand)]:
            if stand:
                gb_str = f", {stand.games_back} GB" if stand.games_back > 0 else ""
                lines.append(
                    f"- {label} ({stand.team_name}): {stand.conference} #{stand.conference_rank} ({stand.overall_record}){gb_str}"
                )

    # Key Players
    home_players = event.home_players
    away_players = event.away_players
    if home_players or away_players:
        lines.append("\n**Key Players**")
        for label, players in [("Home", home_players), ("Away", away_players)]:
            if players and players.players:
                top_players = players.players[:3]  # Show top 3
                player_strs = []
                for p in top_players:
                    name = p.get("name", "Unknown")
                    ppg = p.get("ppg", p.get("avgPointsPerGame", 0))
                    if ppg:
                        player_strs.append(f"{name} ({ppg:.1f} PPG)")
                    else:
                        player_strs.append(name)
                if player_strs:
                    lines.append(
                        f"- {label} ({players.team_name}): {', '.join(player_strs)}"
                    )

    return "\n".join(lines)


def _format_default(event: DataEvent) -> str:
    """Default formatter for unknown event types."""
    event_type = event.event_type
    event_dict = event.to_dict()
    return f"[{event_type}]: {json.dumps(event_dict, default=str, ensure_ascii=False)}"


_EVENT_FORMATTERS: dict[str, Any] = {
    # Shared web search event formatters
    **WEBSEARCH_EVENT_FORMATTERS,
    "game_initialize": _format_game_initialize,
    "game_start": _format_game_start,
    "game_result": _format_game_result,
    "nba_game_update": _format_game_update,
    "odds_update": _format_odds_update,
    "nba_play": _format_play_by_play,
    "pregame_stats": _format_pregame_stats,
}


def format_event(event: DataEvent | BetExecutedPayload | BetSettledPayload) -> str:
    """Format a DataEvent or betting payload into LLM-friendly text."""
    # Handle betting payloads
    if isinstance(event, BetExecutedPayload):
        return _format_bet_executed(event)
    if isinstance(event, BetSettledPayload):
        return _format_bet_settled(event)

    # Handle DataEvent
    event_type = event.event_type
    # Strip "event." prefix if present (new format: event.game_update -> game_update)
    if event_type.startswith("event."):
        event_type = event_type[6:]
    formatter = _EVENT_FORMATTERS.get(event_type, _format_default)
    return formatter(event)


def parse_response_content(content: Any) -> tuple[str, list[dict] | None]:
    """Parse LLM response content into text and tool calls.

    Handles content as a list of dicts with types:
    - "text": Contains text content
    - "tool_use": Contains tool call info
    - "tool_result": Contains tool result info

    Args:
        content: Response content (can be list[dict], str, or None)

    Returns:
        Tuple of (text_content, tool_calls)
    """
    if content is None:
        return "", None

    if not isinstance(content, list):
        return str(content), None

    text_parts = []
    tool_calls = []

    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            text_parts.append(item.get("text", ""))
        elif item_type in ("tool_use", "tool_result"):
            tool_calls.append(item)

    return "".join(text_parts), tool_calls or None
