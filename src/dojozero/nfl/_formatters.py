"""Event formatters for NFL betting agent."""

import json
from typing import Any

from dojozero.data._models import (
    DataEvent,
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    OddsUpdateEvent,
    VenueInfo,
)
from dojozero.data.nfl._events import (
    NFLDriveEvent,
    NFLGameUpdateEvent,
    NFLPlayEvent,
)
from dojozero.data.websearch._formatters import WEBSEARCH_EVENT_FORMATTERS
from dojozero.betting._models import BetExecutedPayload, BetSettledPayload
from dojozero.betting._formatters import (
    format_bet_executed,
    format_bet_settled,
    format_pregame_stats,
)


def _format_nfl_game_initialize(event: GameInitializeEvent) -> str:
    """Format GameInitializeEvent to readable text."""
    home_team = str(event.home_team)
    away_team = str(event.away_team)
    game_time = event.game_time

    time_str = f" at {game_time.strftime('%Y-%m-%d %H:%M UTC')}"

    venue = event.venue
    venue_str = (
        f" @ {venue.name}" if isinstance(venue, VenueInfo) and venue.name else ""
    )

    return f"[NFL Game Initialized] {away_team} @ {home_team}{time_str}{venue_str}"


def _format_nfl_game_start(event: GameStartEvent) -> str:
    """Format GameStartEvent to readable text."""
    return f"[NFL Game Started] Game ID: {event.game_id} - Kickoff!"


def _format_nfl_game_result(event: GameResultEvent) -> str:
    """Format GameResultEvent to readable text."""
    home_score = event.home_score
    away_score = event.away_score
    home_team = event.home_team_name or "Home"
    away_team = event.away_team_name or "Away"
    winner_str = (
        home_team
        if event.winner == "home"
        else away_team
        if event.winner == "away"
        else "Tie"
    )

    return f"[NFL Game Finished] {winner_str} wins! Final Score: {away_team} {away_score} - {home_team} {home_score}"


def _format_nfl_game_update(event: NFLGameUpdateEvent) -> str:
    """Format NFLGameUpdateEvent to readable text."""
    quarter = event.period
    game_clock = event.game_clock
    possession = event.possession
    down = event.down
    distance = event.distance
    yard_line = event.yard_line
    home = event.home_team_stats
    away = event.away_team_stats

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
        f"{away.team_name} ({away.team_abbreviation}): {away.score}",
        f"{home.team_name} ({home.team_abbreviation}): {home.score}",
    ]

    return "\n".join(lines)


def _format_nfl_odds_update(event: OddsUpdateEvent) -> str:
    """Format OddsUpdateEvent to readable text."""
    provider = event.odds.provider
    provider_str = f" ({provider})" if provider else ""

    lines = [f"[NFL Odds Update]{provider_str}"]

    ml = event.odds.moneyline
    if ml:
        lines.append(
            f"- Home: {ml.home_odds:.2f} ({ml.home_probability * 100:.1f}% implied)"
        )
        lines.append(
            f"- Away: {ml.away_odds:.2f} ({ml.away_probability * 100:.1f}% implied)"
        )

    for sp in event.odds.spreads:
        spread_str = f"+{sp.spread}" if sp.spread > 0 else str(sp.spread)
        lines.append(
            f"- Spread: Home {spread_str} (Home: {sp.home_odds:.2f}, Away: {sp.away_odds:.2f})"
        )

    for tot in event.odds.totals:
        lines.append(
            f"- Total: O/U {tot.total:.1f} (Over: {tot.over_odds:.2f}, Under: {tot.under_odds:.2f})"
        )

    return "\n".join(lines)


def _format_nfl_play(event: NFLPlayEvent) -> str:
    """Format NFLPlayEvent to readable text."""
    quarter = event.period
    game_clock = event.clock
    play_type = event.play_type
    description = event.description
    yards_gained = event.yards_gained
    team_abbreviation = event.team_abbreviation
    home_score = event.home_score
    away_score = event.away_score
    is_scoring_play = event.is_scoring_play
    is_turnover = event.is_turnover
    down = event.down
    distance = event.distance
    yard_line = event.yard_line

    quarter_name = f"Q{quarter}" if quarter <= 4 else f"OT{quarter - 4}"
    team_str = f" ({team_abbreviation})" if team_abbreviation else ""
    yards_str = f" | {yards_gained:+d} yards" if yards_gained != 0 else ""

    # Format down & distance (only for scrimmage plays where down > 0)
    if down > 0:
        down_str = f" | {down}&{distance} at {yard_line}"
    else:
        down_str = ""

    # Add special markers
    markers = []
    if is_scoring_play:
        markers.append("SCORE")
    if is_turnover:
        markers.append("TURNOVER")
    marker_str = f" [{', '.join(markers)}]" if markers else ""

    return f"[NFL Play] {quarter_name} {game_clock}{down_str} | {play_type.upper()}{team_str}: {description}{yards_str}{marker_str} [Score: {away_score}-{home_score}]"


def _format_nfl_drive(event: NFLDriveEvent) -> str:
    """Format NFLDriveEvent to readable text."""
    team_tricode = event.team_tricode
    plays_count = event.plays_count
    yards = event.yards
    time_elapsed = event.time_elapsed
    result = event.result
    is_score = event.is_score
    points_scored = event.points_scored

    result_str = result
    if is_score and points_scored > 0:
        result_str = f"{result} ({points_scored} pts)"

    return f"[NFL Drive] {team_tricode}: {plays_count} plays, {yards} yards, {time_elapsed} → {result_str}"


def _format_default(event: DataEvent) -> str:
    """Default formatter for unknown event types."""
    event_type = event.event_type
    event_dict = event.to_dict()
    return f"[{event_type}]: {json.dumps(event_dict, default=str, ensure_ascii=False)}"


_EVENT_FORMATTERS: dict[str, Any] = {
    # Shared web search event formatters
    **WEBSEARCH_EVENT_FORMATTERS,
    # Unified lifecycle events
    "game_initialize": _format_nfl_game_initialize,
    "game_start": _format_nfl_game_start,
    "game_result": _format_nfl_game_result,
    "odds_update": _format_nfl_odds_update,
    # NFL-specific event types
    "nfl_game_update": _format_nfl_game_update,
    "nfl_play": _format_nfl_play,
    "nfl_drive": _format_nfl_drive,
    # Stats insight events
    "pregame_stats": format_pregame_stats,
}


def format_event(event: DataEvent | BetExecutedPayload | BetSettledPayload) -> str:
    """Format a DataEvent or betting payload into LLM-friendly text."""
    # Handle betting payloads
    if isinstance(event, BetExecutedPayload):
        return format_bet_executed(event)
    if isinstance(event, BetSettledPayload):
        return format_bet_settled(event)

    # Handle DataEvent
    event_type = event.event_type
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
