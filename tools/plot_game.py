#!/usr/bin/env python3
"""Visualize a game from a JSONL event log.

Plots home-team win probability over time with key plays annotated at
major odds swings. Score margin is shown on a secondary y-axis.

Usage:
    python tools/plot_game.py outputs/sched-nba-401866755.jsonl
    python tools/plot_game.py outputs/sched-nba-401866755.jsonl --swing-threshold 0.03
    python tools/plot_game.py outputs/sched-nba-401866755.jsonl -o game.html
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OddsPoint:
    ts: datetime
    home_prob: float


@dataclass(slots=True)
class PlayEvent:
    ts: datetime
    period: int
    clock: str
    description: str
    home_score: int
    away_score: int
    action_type: str
    is_scoring: bool
    score_value: int
    player_name: str


@dataclass(slots=True)
class GameInfo:
    game_id: str = ""
    home_name: str = ""
    away_name: str = ""
    home_tricode: str = ""
    away_tricode: str = ""
    home_color: str = ""
    away_color: str = ""
    final_home: int = 0
    final_away: int = 0


@dataclass(slots=True)
class GameData:
    info: GameInfo = field(default_factory=GameInfo)
    odds: list[OddsPoint] = field(default_factory=list)
    plays: list[PlayEvent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    # Handle both Z suffix and +00:00
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def load_game(path: Path) -> GameData:
    gd = GameData()

    with open(path) as f:
        for line in f:
            e = json.loads(line)
            et = e["event_type"]

            if et == "event.game_initialize":
                gd.info.game_id = e["game_id"]
                gd.info.home_name = e["home_team"]["name"]
                gd.info.away_name = e["away_team"]["name"]
                gd.info.home_tricode = e["home_team"]["tricode"]
                gd.info.away_tricode = e["away_team"]["tricode"]
                gd.info.home_color = e["home_team"].get("color", "1f77b4")
                gd.info.away_color = e["away_team"].get("color", "ff7f0e")

            elif et == "event.odds_update":
                ts = parse_ts(e.get("game_timestamp") or e["timestamp"])
                if ts is None:
                    continue
                hp = e["odds"]["moneyline"]["home_probability"]
                gd.odds.append(OddsPoint(ts=ts, home_prob=hp))

            elif et == "event.nba_play":
                ts = parse_ts(e.get("game_timestamp") or e["timestamp"])
                if ts is None:
                    continue
                gd.plays.append(
                    PlayEvent(
                        ts=ts,
                        period=e["period"],
                        clock=e["clock"],
                        description=e["description"],
                        home_score=e["home_score"],
                        away_score=e["away_score"],
                        action_type=e["action_type"],
                        is_scoring=e["is_scoring_play"],
                        score_value=e["score_value"],
                        player_name=e.get("player_name", ""),
                    )
                )

            elif et == "event.game_result":
                gd.info.final_home = e["home_score"]
                gd.info.final_away = e["away_score"]

    gd.odds.sort(key=lambda o: o.ts)
    gd.plays.sort(key=lambda p: p.ts)
    return gd


# ---------------------------------------------------------------------------
# Swing detection
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SwingEvent:
    """An odds swing paired with the play that likely caused it."""

    odds_idx: int
    delta: float  # signed: positive = home improved
    play: PlayEvent | None
    odds_before: float
    odds_after: float


def detect_swings(gd: GameData, threshold: float) -> list[SwingEvent]:
    swings: list[SwingEvent] = []
    for i in range(1, len(gd.odds)):
        delta = gd.odds[i].home_prob - gd.odds[i - 1].home_prob
        if abs(delta) < threshold:
            continue

        # Find the nearest play that happened between the two odds timestamps
        t_before = gd.odds[i - 1].ts
        t_after = gd.odds[i].ts
        candidates = [p for p in gd.plays if t_before <= p.ts <= t_after]

        # Only match scoring plays
        play = None
        if candidates:
            scoring = [p for p in candidates if p.is_scoring]
            if scoring:
                play = scoring[-1]

        # If no scoring play in window, search backwards for the most recent one
        if play is None:
            before_scoring = [p for p in gd.plays if p.is_scoring and p.ts <= t_after]
            if before_scoring:
                play = before_scoring[-1]

        swings.append(
            SwingEvent(
                odds_idx=i,
                delta=delta,
                play=play,
                odds_before=gd.odds[i - 1].home_prob,
                odds_after=gd.odds[i].home_prob,
            )
        )

    return swings


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def _period_label(period: int) -> str:
    if period <= 4:
        return f"Q{period}"
    return f"OT{period - 4}"


def build_figure(gd: GameData, swings: list[SwingEvent]) -> go.Figure:
    info = gd.info
    title = (
        f"{info.away_tricode} {info.final_away} @ {info.home_tricode} {info.final_home}"
        if info.final_home
        else f"{info.away_tricode} @ {info.home_tricode}"
    )

    fig = go.Figure()

    # --- Odds line ---
    odds_ts = [o.ts for o in gd.odds]
    odds_prob = [o.home_prob * 100 for o in gd.odds]

    fig.add_trace(
        go.Scatter(
            x=odds_ts,
            y=odds_prob,
            mode="lines",
            name=f"{info.home_tricode} Win %",
            line=dict(color=f"#{info.home_color}", width=2),
            hovertemplate="%{x|%H:%M:%S}<br>%{y:.1f}%<extra></extra>",
            yaxis="y",
        )
    )

    # 50% reference line
    fig.add_hline(y=50, line_dash="dash", line_color="gray", opacity=0.5)

    # --- Score margin on secondary axis ---
    # Build score margin from plays (only scoring plays update the score)
    score_ts = []
    score_margins = []
    last_home, last_away = 0, 0
    for p in gd.plays:
        if p.home_score != last_home or p.away_score != last_away:
            score_ts.append(p.ts)
            score_margins.append(p.home_score - p.away_score)
            last_home, last_away = p.home_score, p.away_score

    if score_ts:
        fig.add_trace(
            go.Scatter(
                x=score_ts,
                y=score_margins,
                mode="lines",
                name="Score Margin",
                line=dict(color="rgba(100,100,100,0.3)", width=1.5, dash="dot"),
                hovertemplate=("%{x|%H:%M:%S}<br>Margin: %{y:+d}<extra></extra>"),
                yaxis="y2",
            )
        )

    # --- Swing annotations ---
    swing_ts = [gd.odds[s.odds_idx].ts for s in swings]
    swing_prob = [gd.odds[s.odds_idx].home_prob * 100 for s in swings]
    swing_colors = [
        f"#{info.home_color}" if s.delta > 0 else f"#{info.away_color}" for s in swings
    ]
    swing_sizes = [min(6 + abs(s.delta) * 80, 20) for s in swings]
    swing_text = []
    for s in swings:
        if s.play:
            label = f"{_period_label(s.play.period)} {s.play.clock}"
            desc = s.play.description
            score = f"{info.home_tricode} {s.play.home_score} - {info.away_tricode} {s.play.away_score}"
            delta_str = f"{s.delta * 100:+.1f}%"
            swing_text.append(
                f"<b>{label}</b><br>{desc}<br>{score}<br>Swing: {delta_str}"
            )
        else:
            swing_text.append(f"Swing: {s.delta * 100:+.1f}%")

    fig.add_trace(
        go.Scatter(
            x=swing_ts,
            y=swing_prob,
            mode="markers",
            name="Key Moments",
            marker=dict(
                color=swing_colors,
                size=swing_sizes,
                line=dict(width=1, color="white"),
                symbol="circle",
            ),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=swing_text,
        )
    )

    # --- Text labels for top swings ---
    top_swings = sorted(swings, key=lambda s: abs(s.delta), reverse=True)[:8]
    for s in top_swings:
        if not s.play:
            continue
        t = gd.odds[s.odds_idx].ts
        y = gd.odds[s.odds_idx].home_prob * 100
        # Truncate description for label
        desc = s.play.description
        if len(desc) > 50:
            desc = desc[:47] + "..."
        label = f"{_period_label(s.play.period)} {s.play.clock}<br>{desc}"
        fig.add_annotation(
            x=t,
            y=y,
            text=label,
            showarrow=True,
            arrowhead=2,
            arrowsize=0.8,
            ax=0,
            ay=-40 if s.delta > 0 else 40,
            font=dict(size=9),
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="rgba(0,0,0,0.2)",
            borderwidth=1,
        )

    # --- Quarter separators ---
    # Find first play of each period
    seen_periods: set[int] = set()
    for p in gd.plays:
        if p.period not in seen_periods:
            seen_periods.add(p.period)
            fig.add_shape(
                type="line",
                x0=p.ts,
                x1=p.ts,
                y0=0,
                y1=1,
                yref="y domain",
                line=dict(dash="dash", color="rgba(0,0,0,0.15)"),
            )
            fig.add_annotation(
                x=p.ts,
                y=1,
                yref="y domain",
                text=_period_label(p.period),
                showarrow=False,
                font=dict(size=10),
                yshift=10,
            )

    # --- Layout ---
    fig.update_layout(
        title=dict(text=title, font=dict(size=20)),
        xaxis=dict(
            title="Game Time",
            tickformat="%H:%M",
        ),
        yaxis=dict(
            title=f"{info.home_tricode} Win Probability (%)",
            range=[0, 100],
            ticksuffix="%",
        ),
        yaxis2=dict(
            title="Score Margin (Home)",
            overlaying="y",
            side="right",
            showgrid=False,
            zeroline=True,
            zerolinecolor="rgba(100,100,100,0.3)",
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        template="plotly_white",
        hovermode="x unified",
        height=600,
        margin=dict(l=60, r=60, t=80, b=60),
        updatemenus=[
            dict(
                type="buttons",
                direction="left",
                buttons=[
                    dict(
                        label="Reset View",
                        method="relayout",
                        args=[
                            {
                                "xaxis.autorange": True,
                                "yaxis.autorange": False,
                                "yaxis.range": [0, 100],
                                "yaxis2.autorange": True,
                            }
                        ],
                    )
                ],
                showactive=False,
                x=1,
                xanchor="right",
                y=-0.15,
                yanchor="top",
            )
        ],
    )

    return fig


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Plot game odds & key plays from JSONL"
    )
    parser.add_argument("jsonl", type=Path, help="Path to game JSONL file")
    parser.add_argument(
        "--swing-threshold",
        "-t",
        type=float,
        default=0.03,
        help="Min odds change to flag as a swing (default: 0.03 = 3%%)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output HTML path (default: <jsonl_stem>.html alongside input)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Don't auto-open the HTML in browser",
    )
    args = parser.parse_args()

    gd = load_game(args.jsonl)
    swings = detect_swings(gd, args.swing_threshold)

    print(f"Loaded {len(gd.odds)} odds updates, {len(gd.plays)} plays")
    print(f"Detected {len(swings)} swings (threshold={args.swing_threshold:.0%})")

    fig = build_figure(gd, swings)

    out = args.output or args.jsonl.with_suffix(".html")
    fig.write_html(out, include_plotlyjs="cdn")
    print(f"Wrote {out}")

    if not args.no_open:
        webbrowser.open(f"file://{out.resolve()}")


if __name__ == "__main__":
    main()
