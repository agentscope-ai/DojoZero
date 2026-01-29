"""Event formatters for NFL betting agent."""

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


def _format_nfl_game_initialize(event: DataEvent) -> str:
    """Format NFLGameInitializeEvent to readable text."""
    home_team = getattr(event, "home_team", "Unknown")
    away_team = getattr(event, "away_team", "Unknown")
    game_time = getattr(event, "game_time", None)
    venue = getattr(event, "venue", "")
    week = getattr(event, "week", 0)

    time_str = ""
    if game_time:
        if isinstance(game_time, datetime):
            time_str = f" at {game_time.strftime('%Y-%m-%d %H:%M UTC')}"
        else:
            time_str = f" at {game_time}"

    week_str = f" (Week {week})" if week else ""
    venue_str = f" @ {venue}" if venue else ""

    return f"[NFL Game Initialized] {away_team} @ {home_team}{week_str}{time_str}{venue_str}"


def _format_nfl_game_start(event: DataEvent) -> str:
    """Format NFLGameStartEvent to readable text."""
    event_id = getattr(event, "event_id", "")
    return f"[NFL Game Started] Event ID: {event_id} - Kickoff!"


def _format_nfl_game_result(event: DataEvent) -> str:
    """Format NFLGameResultEvent to readable text."""
    winner = getattr(event, "winner", "")
    final_score = getattr(event, "final_score", {})
    home_team = getattr(event, "home_team", "Home")
    away_team = getattr(event, "away_team", "Away")

    home_score = final_score.get("home", "?")
    away_score = final_score.get("away", "?")
    winner_str = (
        home_team if winner == "home" else away_team if winner == "away" else "Tie"
    )

    return f"[NFL Game Finished] {winner_str} wins! Final Score: {away_team} {away_score} - {home_team} {home_score}"


def _format_nfl_game_update(event: DataEvent) -> str:
    """Format NFLGameUpdateEvent to readable text."""
    quarter = getattr(event, "quarter", 0)
    game_clock = getattr(event, "game_clock", "")
    possession = getattr(event, "possession", "")
    down = getattr(event, "down", 0)
    distance = getattr(event, "distance", 0)
    yard_line = getattr(event, "yard_line", "")
    home_team = getattr(event, "home_team", {})
    away_team = getattr(event, "away_team", {})

    # Get team info from dicts
    home_name = (
        home_team.get("teamName", "Home") if isinstance(home_team, dict) else "Home"
    )
    home_abbrev = (
        home_team.get("teamAbbreviation", "") if isinstance(home_team, dict) else ""
    )
    home_score = home_team.get("score", 0) if isinstance(home_team, dict) else 0

    away_name = (
        away_team.get("teamName", "Away") if isinstance(away_team, dict) else "Away"
    )
    away_abbrev = (
        away_team.get("teamAbbreviation", "") if isinstance(away_team, dict) else ""
    )
    away_score = away_team.get("score", 0) if isinstance(away_team, dict) else 0

    # Format quarter
    if quarter <= 4:
        quarter_name = f"Q{quarter}"
    else:
        quarter_name = f"OT{quarter - 4}"

    clock_str = f" | {game_clock}" if game_clock else ""
    possession_str = f" | Ball: {possession}" if possession else ""
    down_str = f" | {down} & {distance} at {yard_line}" if down > 0 else ""

    lines = [
        f"[NFL Game Update] {quarter_name}{clock_str}{possession_str}{down_str}",
        f"{away_name} ({away_abbrev}): {away_score}",
        f"{home_name} ({home_abbrev}): {home_score}",
    ]

    return "\n".join(lines)


def _format_nfl_odds_update(event: DataEvent) -> str:
    """Format NFLOddsUpdateEvent to readable text."""
    moneyline_home = getattr(event, "moneyline_home", 0)
    moneyline_away = getattr(event, "moneyline_away", 0)
    spread = getattr(event, "spread", 0.0)
    over_under = getattr(event, "over_under", 0.0)
    provider = getattr(event, "provider", "")
    home_team = getattr(event, "home_team", "Home")
    away_team = getattr(event, "away_team", "Away")

    # Calculate implied probabilities from moneyline
    def moneyline_to_prob(ml: int) -> float:
        if ml == 0:
            return 0.0
        if ml > 0:
            return 100 / (ml + 100)
        else:
            return abs(ml) / (abs(ml) + 100)

    home_prob = moneyline_to_prob(moneyline_home)
    away_prob = moneyline_to_prob(moneyline_away)

    # Format moneyline display
    def format_ml(ml: int) -> str:
        if ml > 0:
            return f"+{ml}"
        return str(ml)

    provider_str = f" ({provider})" if provider else ""
    spread_str = f"+{spread}" if spread > 0 else str(spread)

    lines = [
        f"[NFL Odds Update]{provider_str}",
        f"- {home_team}: {format_ml(moneyline_home)} ({home_prob * 100:.1f}% implied)",
        f"- {away_team}: {format_ml(moneyline_away)} ({away_prob * 100:.1f}% implied)",
        f"- Spread: {home_team} {spread_str}",
        f"- Over/Under: {over_under}",
    ]

    return "\n".join(lines)


def _format_nfl_play(event: DataEvent) -> str:
    """Format NFLPlayEvent to readable text."""
    quarter = getattr(event, "quarter", 0)
    game_clock = getattr(event, "game_clock", "")
    play_type = getattr(event, "play_type", "")
    description = getattr(event, "description", "")
    yards_gained = getattr(event, "yards_gained", 0)
    team_abbreviation = getattr(event, "team_abbreviation", "")
    home_score = getattr(event, "home_score", 0)
    away_score = getattr(event, "away_score", 0)
    is_scoring_play = getattr(event, "is_scoring_play", False)
    is_turnover = getattr(event, "is_turnover", False)

    quarter_name = f"Q{quarter}" if quarter <= 4 else f"OT{quarter - 4}"
    team_str = f" ({team_abbreviation})" if team_abbreviation else ""
    yards_str = f" | {yards_gained:+d} yards" if yards_gained != 0 else ""

    # Add special markers
    markers = []
    if is_scoring_play:
        markers.append("SCORE")
    if is_turnover:
        markers.append("TURNOVER")
    marker_str = f" [{', '.join(markers)}]" if markers else ""

    return f"[NFL Play] {quarter_name} {game_clock} | {play_type.upper()}{team_str}: {description}{yards_str}{marker_str} [Score: {away_score}-{home_score}]"


def _format_nfl_drive(event: DataEvent) -> str:
    """Format NFLDriveEvent to readable text."""
    team_abbreviation = getattr(event, "team_abbreviation", "")
    plays = getattr(event, "plays", 0)
    yards = getattr(event, "yards", 0)
    time_elapsed = getattr(event, "time_elapsed", "")
    result = getattr(event, "result", "")
    is_score = getattr(event, "is_score", False)
    points_scored = getattr(event, "points_scored", 0)

    result_str = result
    if is_score and points_scored > 0:
        result_str = f"{result} ({points_scored} pts)"

    return f"[NFL Drive] {team_abbreviation}: {plays} plays, {yards} yards, {time_elapsed} → {result_str}"


def _format_default(event: DataEvent) -> str:
    """Default formatter for unknown event types."""
    event_type = getattr(event, "event_type", "unknown")
    event_dict = event.to_dict() if hasattr(event, "to_dict") else str(event)
    return f"[{event_type}]: {json.dumps(event_dict, default=str, ensure_ascii=False)}"


_EVENT_FORMATTERS: dict[str, Any] = {
    # Shared web search event types
    "injury_report": _format_injury_summary,
    "power_ranking": _format_power_ranking,
    "expert_prediction": _format_expert_prediction,
    # NFL-specific event types
    "nfl_game_initialize": _format_nfl_game_initialize,
    "nfl_game_start": _format_nfl_game_start,
    "nfl_game_result": _format_nfl_game_result,
    "nfl_game_update": _format_nfl_game_update,
    "nfl_odds_update": _format_nfl_odds_update,
    "nfl_play": _format_nfl_play,
    "nfl_drive": _format_nfl_drive,
}


def format_event(event: DataEvent) -> str:
    """Format a DataEvent into LLM-friendly text."""
    event_type = getattr(event, "event_type", "unknown")
    # Strip "event." prefix if present (new format: event.nfl_game_update -> nfl_game_update)
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
