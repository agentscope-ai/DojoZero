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
from dojozero.data.nba._events import NBAGameUpdateEvent, NBAPlayEvent
from dojozero.data.websearch._events import (
    ExpertPredictionEvent,
    InjuryReportEvent,
    PowerRankingEvent,
)


def _format_injury_summary(event: InjuryReportEvent) -> str:
    """Format InjuryReportEvent to readable text."""
    lines = ["[Injury Report Update]"]
    if event.summary:
        lines.append(f"\n{event.summary}")

    if event.injured_players:
        lines.append("\n**Injured Players by Team:**")
        for team, players in event.injured_players.items():
            if players:
                players_str = ", ".join(players)
                lines.append(f"- {team}: {players_str}")

    return "\n".join(lines)


def _format_power_ranking(event: PowerRankingEvent) -> str:
    """Format PowerRankingEvent to readable text."""
    lines = ["[Power Rankings Update]"]

    for source, team_rankings in event.rankings.items():
        lines.append(f"\n**Source: {source}**")
        for rank_info in team_rankings[:10]:
            rank = rank_info.get("rank", "?")
            team = rank_info.get("team", "Unknown")
            record = rank_info.get("record", "")
            record_str = f" ({record})" if record else ""
            lines.append(f"{rank}. {team}{record_str}")

    return "\n".join(lines)


def _format_expert_prediction(event: ExpertPredictionEvent) -> str:
    """Format ExpertPredictionEvent to readable text."""
    lines = ["[Expert Predictions]"]

    for pred in event.predictions:
        source = pred.get("source", "Unknown")
        expert = pred.get("expert", "")
        prediction = pred.get("prediction", "")
        confidence = pred.get("confidence", "")

        expert_str = f" ({expert})" if expert else ""
        conf_str = f" [Confidence: {confidence}]" if confidence else ""
        lines.append(f"\n**{source}{expert_str}**{conf_str}")
        if prediction:
            lines.append(f"{prediction}")

    return "\n".join(lines)


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


def _format_default(event: DataEvent) -> str:
    """Default formatter for unknown event types."""
    event_type = event.event_type
    event_dict = event.to_dict()
    return f"[{event_type}]: {json.dumps(event_dict, default=str, ensure_ascii=False)}"


_EVENT_FORMATTERS: dict[str, Any] = {
    "injury_report": _format_injury_summary,
    "power_ranking": _format_power_ranking,
    "expert_prediction": _format_expert_prediction,
    "game_initialize": _format_game_initialize,
    "game_start": _format_game_start,
    "game_result": _format_game_result,
    "nba_game_update": _format_game_update,
    "odds_update": _format_odds_update,
    "nba_play": _format_play_by_play,
}


def format_event(event: DataEvent) -> str:
    """Format a DataEvent into LLM-friendly text."""
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
