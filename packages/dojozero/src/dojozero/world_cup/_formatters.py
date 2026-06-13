"""Event formatters for World Cup betting agent."""

import json
from typing import Any

from dojozero.data._models import (
    DataEvent,
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    OddsUpdateEvent,
)
from dojozero.data.socialmedia._formatters import SOCIALMEDIA_EVENT_FORMATTERS
from dojozero.data.websearch._formatters import WEBSEARCH_EVENT_FORMATTERS
from dojozero.data.world_cup._events import (
    WorldCupGameUpdateEvent,
    WorldCupPlayEvent,
)
from dojozero.betting._formatters import (
    format_bet_executed,
    format_bet_settled,
    format_pregame_stats,
)
from dojozero.betting._models import BetExecutedPayload, BetSettledPayload


def _period_label(period: int) -> str:
    """Human-friendly period label for soccer.

    1H, 2H, ET1, ET2, PEN.
    """
    if period <= 0:
        return "?"
    if period == 1:
        return "1H"
    if period == 2:
        return "2H"
    if period == 3:
        return "ET1"
    if period == 4:
        return "ET2"
    if period == 5:
        return "PEN"
    return f"P{period}"


def _format_game_initialize(event: GameInitializeEvent) -> str:
    home_team = str(event.home_team)
    away_team = str(event.away_team)
    time_str = (
        f" at {event.game_time.strftime('%Y-%m-%d %H:%M UTC')}"
        if event.game_time
        else ""
    )
    return f"[Match Initialized] {away_team} @ {home_team}{time_str}"


def _format_game_start(event: GameStartEvent) -> str:
    return f"[Kickoff] Match ID: {event.game_id}"


def _format_game_result(event: GameResultEvent) -> str:
    winner_str = (
        event.home_team_name or "Home Team"
        if event.winner == "home"
        else event.away_team_name or "Away Team"
        if event.winner == "away"
        else "Draw"
    )
    return (
        f"[Full Time] {winner_str} | "
        f"Final: {event.home_team_name or 'Home'} {event.home_score} - "
        f"{event.away_score} {event.away_team_name or 'Away'}"
    )


def _format_game_update(event: WorldCupGameUpdateEvent) -> str:
    home = event.home_team_stats
    away = event.away_team_stats
    period_name = _period_label(event.period)
    clock_str = f" | {event.game_clock}" if event.game_clock else ""

    def team_label(team_name: str, team_tricode: str, side: str) -> str:
        name = team_name or f"{side} Team"
        code = f" ({team_tricode})" if team_tricode else ""
        return f"{name}{code} ({side})"

    lines = [
        f"[Match Update] {period_name}{clock_str}",
        f"{team_label(home.team_name, home.team_tricode, 'Home')}: "
        f"{event.home_score}  "
        f"shots {home.total_shots}/{home.shots_on_target}  "
        f"poss {home.possession_pct:.1f}%",
        f"{team_label(away.team_name, away.team_tricode, 'Away')}: "
        f"{event.away_score}  "
        f"shots {away.total_shots}/{away.shots_on_target}  "
        f"poss {away.possession_pct:.1f}%",
    ]
    return "\n".join(lines)


def _format_odds_update(event: OddsUpdateEvent) -> str:
    lines = ["[Odds Update]"]
    ml = event.odds.moneyline
    if ml:
        lines.append(
            f"- Home: {ml.home_odds:.2f} ({ml.home_probability * 100:.1f}% implied)"
        )
        lines.append(
            f"- Away: {ml.away_odds:.2f} ({ml.away_probability * 100:.1f}% implied)"
        )
    for sp in event.odds.spreads:
        lines.append(
            f"- Spread: {sp.spread:+.1f} "
            f"(Home: {sp.home_odds:.2f}, Away: {sp.away_odds:.2f})"
        )
    for tot in event.odds.totals:
        lines.append(
            f"- Total: O/U {tot.total:.1f} "
            f"(Over: {tot.over_odds:.2f}, Under: {tot.under_odds:.2f})"
        )
    return "\n".join(lines)


def _format_play_by_play(event: WorldCupPlayEvent) -> str:
    period_name = _period_label(event.period)
    clock = event.clock or ""
    action = event.action_type.upper() if event.action_type else "PLAY"
    player_str = f" [{event.player_name}]" if event.player_name else ""
    team_str = f" ({event.team_tricode})" if event.team_tricode else ""
    description = event.description or ""
    return (
        f"[Play] {period_name} {clock} | {action}{team_str}{player_str}: "
        f"{description} [Score: {event.away_score}-{event.home_score}]"
    )


def _format_default(event: DataEvent) -> str:
    return f"[{event.event_type}]: {json.dumps(event.to_dict(), default=str, ensure_ascii=False)}"


_EVENT_FORMATTERS: dict[str, Any] = {
    **WEBSEARCH_EVENT_FORMATTERS,
    **SOCIALMEDIA_EVENT_FORMATTERS,
    "game_initialize": _format_game_initialize,
    "game_start": _format_game_start,
    "game_result": _format_game_result,
    "world_cup_game_update": _format_game_update,
    "odds_update": _format_odds_update,
    "world_cup_play": _format_play_by_play,
    "pregame_stats": format_pregame_stats,
}


def format_event(
    event: DataEvent | BetExecutedPayload | BetSettledPayload,
) -> str:
    """Format a DataEvent or betting payload as LLM-friendly text."""
    if isinstance(event, BetExecutedPayload):
        return format_bet_executed(event)
    if isinstance(event, BetSettledPayload):
        return format_bet_settled(event)

    event_type = event.event_type
    if event_type.startswith("event."):
        event_type = event_type[6:]
    formatter = _EVENT_FORMATTERS.get(event_type, _format_default)
    return formatter(event)


def parse_response_content(content: Any) -> tuple[str, list[dict] | None]:
    """Parse LLM response content into (text, tool_calls)."""
    if content is None:
        return "", None
    if not isinstance(content, list):
        return str(content), None
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            text_parts.append(item.get("text", ""))
        elif item_type in ("tool_use", "tool_result"):
            tool_calls.append(item)
    return "".join(text_parts), tool_calls or None


__all__ = ["format_event", "parse_response_content"]
