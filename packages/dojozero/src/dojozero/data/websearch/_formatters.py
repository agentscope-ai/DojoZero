"""Shared formatters for web search insight events.

These formatters are sport-agnostic — the events carry sport-specific content
(team names, player names) but the formatting logic is identical across sports.
"""

from dojozero.data.websearch._events import (
    ExpertPredictionEvent,
    InjuryReportEvent,
    PowerRankingEvent,
)


def format_injury_report(event: InjuryReportEvent) -> str:
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


def format_power_ranking(event: PowerRankingEvent) -> str:
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


def format_expert_prediction(event: ExpertPredictionEvent) -> str:
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


WEBSEARCH_EVENT_FORMATTERS: dict[str, object] = {
    "injury_report": format_injury_report,
    "power_ranking": format_power_ranking,
    "expert_prediction": format_expert_prediction,
}
