"""Event formatters for NBA moneyline betting agent."""

import json
from datetime import datetime
from typing import Any

from dojozero.data._models import DataEvent


def _format_injury_summary(event: DataEvent) -> str:
    """Format InjurySummaryEvent to readable text."""
    summary = getattr(event, "summary", "")
    injured_players = getattr(event, "injured_players", {})

    lines = ["[Injury Report Update]"]
    if summary:
        lines.append(f"\n{summary}")

    if injured_players:
        lines.append("\n**Injured Players by Team:**")
        for team, players in injured_players.items():
            if players:
                players_str = ", ".join(players)
                lines.append(f"- {team}: {players_str}")

    return "\n".join(lines)


def _format_power_ranking(event: DataEvent) -> str:
    """Format PowerRankingEvent to readable text."""
    rankings = getattr(event, "rankings", {})

    lines = ["[Power Rankings Update]"]

    for source, team_rankings in rankings.items():
        lines.append(f"\n**Source: {source}**")
        for rank_info in team_rankings[:10]:
            rank = rank_info.get("rank", "?")
            team = rank_info.get("team", "Unknown")
            record = rank_info.get("record", "")
            record_str = f" ({record})" if record else ""
            lines.append(f"{rank}. {team}{record_str}")

    return "\n".join(lines)


def _format_expert_prediction(event: DataEvent) -> str:
    """Format ExpertPredictionEvent to readable text."""
    predictions = getattr(event, "predictions", [])

    lines = ["[Expert Predictions]"]

    for pred in predictions:
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


def _format_game_initialize(event: DataEvent) -> str:
    """Format GameInitializeEvent to readable text."""
    home_team = getattr(event, "home_team", "Unknown")
    away_team = getattr(event, "away_team", "Unknown")
    game_time = getattr(event, "game_time", None)

    time_str = ""
    if game_time:
        if isinstance(game_time, datetime):
            time_str = f" at {game_time.strftime('%Y-%m-%d %H:%M UTC')}"
        else:
            time_str = f" at {game_time}"

    return f"[Game Initialized] {away_team} @ {home_team}{time_str}"


def _format_game_start(event: DataEvent) -> str:
    """Format GameStartEvent to readable text."""
    event_id = getattr(event, "event_id", "")
    return f"[Game Started] Event ID: {event_id}"


def _format_game_result(event: DataEvent) -> str:
    """Format GameResultEvent to readable text."""
    winner = getattr(event, "winner", "")
    final_score = getattr(event, "final_score", {})

    home_score = final_score.get("home", "?")
    away_score = final_score.get("away", "?")
    winner_str = (
        "Home Team" if winner == "home" else "Away Team" if winner == "away" else winner
    )

    return f"[Game Finished] {winner_str} wins! Final Score: Home {home_score} - Away {away_score}"


def _format_game_update(event: DataEvent) -> str:
    """Format GameUpdateEvent to readable text."""
    period = getattr(event, "period", 0)
    game_clock = getattr(event, "game_clock", "")
    home_team = getattr(event, "home_team", {})
    away_team = getattr(event, "away_team", {})

    home_name = home_team.get("teamName", "Home")
    home_tricode = home_team.get("teamTricode", "")
    home_score = home_team.get("score", 0)

    away_name = away_team.get("teamName", "Away")
    away_tricode = away_team.get("teamTricode", "")
    away_score = away_team.get("score", 0)

    period_name = f"Q{period}" if period <= 4 else f"OT{period - 4}"
    clock_str = f" | {game_clock}" if game_clock else ""

    lines = [
        f"[Game Update] {period_name}{clock_str}",
        f"{away_name} ({away_tricode}): {away_score}",
        f"{home_name} ({home_tricode}): {home_score}",
    ]

    return "\n".join(lines)


def _format_odds_update(event: DataEvent) -> str:
    """Format OddsUpdateEvent to readable text."""
    home_odds = getattr(event, "home_odds", 1.0)
    away_odds = getattr(event, "away_odds", 1.0)
    home_prob = getattr(event, "home_probability", 0.0)
    away_prob = getattr(event, "away_probability", 0.0)

    lines = [
        "[Odds Update]",
        f"- Home: {home_odds:.2f} ({home_prob * 100:.1f}% implied probability)",
        f"- Away: {away_odds:.2f} ({away_prob * 100:.1f}% implied probability)",
    ]

    return "\n".join(lines)


def _format_play_by_play(event: DataEvent) -> str:
    """Format PlayByPlayEvent to readable text."""
    period = getattr(event, "period", 0)
    clock = getattr(event, "clock", "")
    action_type = getattr(event, "action_type", "")
    player_name = getattr(event, "player_name", "")
    team_tricode = getattr(event, "team_tricode", "")
    description = getattr(event, "description", "")
    home_score = getattr(event, "home_score", 0)
    away_score = getattr(event, "away_score", 0)

    period_name = f"Q{period}" if period <= 4 else f"OT{period - 4}"
    player_str = f" [{player_name}]" if player_name else ""
    team_str = f" ({team_tricode})" if team_tricode else ""

    return f"[Play] {period_name} {clock} | {action_type.upper()}{team_str}{player_str}: {description} [Score: {away_score}-{home_score}]"


def _format_default(event: DataEvent) -> str:
    """Default formatter for unknown event types."""
    event_type = getattr(event, "event_type", "unknown")
    event_dict = event.to_dict() if hasattr(event, "to_dict") else str(event)
    return f"[{event_type}]: {json.dumps(event_dict, default=str, ensure_ascii=False)}"


_EVENT_FORMATTERS: dict[str, Any] = {
    "injury_summary": _format_injury_summary,
    "power_ranking": _format_power_ranking,
    "expert_prediction": _format_expert_prediction,
    "game_initialize": _format_game_initialize,
    "game_start": _format_game_start,
    "game_result": _format_game_result,
    "game_update": _format_game_update,
    "odds_update": _format_odds_update,
    "play_by_play": _format_play_by_play,
}


def format_event(event: DataEvent) -> str:
    """Format a DataEvent into LLM-friendly text."""
    event_type = getattr(event, "event_type", "unknown")
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
