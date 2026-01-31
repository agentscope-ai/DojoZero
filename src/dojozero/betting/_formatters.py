"""Shared event formatters for betting agents."""

from dojozero.data.espn._stats_events import PreGameStatsEvent
from dojozero.betting._models import BetExecutedPayload, BetSettledPayload


def format_bet_executed(payload: BetExecutedPayload) -> str:
    """Format BetExecutedPayload to readable text."""
    return (
        f"[Bet Executed] Bet ID: {payload.bet_id}\n"
        f"- Event: {payload.event_id}\n"
        f"- Selection: {payload.selection}\n"
        f"- Amount: ${payload.amount}\n"
        f"- Odds: {payload.execution_odds}\n"
        f"- Time: {payload.execution_time}"
    )


def format_bet_settled(payload: BetSettledPayload) -> str:
    """Format BetSettledPayload to readable text."""
    outcome_str = payload.outcome.value  # WIN or LOSS
    return (
        f"[Bet Settled] Bet ID: {payload.bet_id}\n"
        f"- Event: {payload.event_id}\n"
        f"- Outcome: {outcome_str}\n"
        f"- Payout: ${payload.payout}\n"
        f"- Winner: {payload.winner}"
    )


def format_pregame_stats(event: PreGameStatsEvent) -> str:
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
