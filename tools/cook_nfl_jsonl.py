"""Post-process a raw concluded-game NFL JSONL for proper replay.

Reads a JSONL produced by the NFL trial runner for a concluded game and outputs
a cleaned version with:
1. Drives interleaved with their plays (proper game chronology)
2. Correct timestamp and game_timestamp on all events
3. Simulated moneyline odds updates every 5 seconds (matching Polymarket polling)

Usage:
    python tools/cook-nfl-jsonl.py <input.jsonl> [--output <output.jsonl>]
"""

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

matplotlib.use("Agg")

# Add parent directory to path to import dojozero modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from dojozero.data._models import MoneylineOdds, OddsInfo, OddsUpdateEvent

logger = logging.getLogger(__name__)

# NFL game clock constants
QUARTER_SECONDS = 900  # 15 minutes per quarter
GAME_CLOCK_MULTIPLIER = 3.25  # Real time ≈ 3.25x game clock
HALFTIME_SECONDS = 1200  # 20 minutes halftime
QUARTER_OFFSETS = {
    1: 0,
    2: int(QUARTER_SECONDS * GAME_CLOCK_MULTIPLIER),
    3: int(2 * QUARTER_SECONDS * GAME_CLOCK_MULTIPLIER + HALFTIME_SECONDS),
    4: int(3 * QUARTER_SECONDS * GAME_CLOCK_MULTIPLIER + HALFTIME_SECONDS),
    5: int(4 * QUARTER_SECONDS * GAME_CLOCK_MULTIPLIER + HALFTIME_SECONDS),
}

PREGAME_EVENT_TYPES = {
    "event.pregame_stats",
    "event.injury_report",
    "event.power_ranking",
    "event.expert_prediction",
    "event.web_search_insight",
}


# ─── Helpers ────────────────────────────────────────────────────────────────


def parse_clock(clock: str) -> int:
    """Parse game clock "MM:SS" to seconds remaining."""
    if not clock:
        return QUARTER_SECONDS
    try:
        parts = clock.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, TypeError):
        pass
    return QUARTER_SECONDS


def compute_game_timestamp(game_start: datetime, period: int, clock: str) -> datetime:
    """Compute approximate wallclock from game clock using 3.25x multiplier."""
    offset = QUARTER_OFFSETS.get(period, QUARTER_OFFSETS[5])
    remaining = parse_clock(clock)
    elapsed_in_quarter = (QUARTER_SECONDS - remaining) * GAME_CLOCK_MULTIPLIER
    return game_start + timedelta(seconds=offset + elapsed_in_quarter)


def game_clock_to_remaining(period: int, clock: str) -> float:
    """Convert period + clock to total game seconds remaining (out of 3600)."""
    remaining_in_quarter = parse_clock(clock)
    quarters_left = max(0, 4 - period)
    return quarters_left * QUARTER_SECONDS + remaining_in_quarter


def is_clock_in_range(
    period: int,
    clock: str,
    start_period: int,
    start_clock: str,
    end_period: int,
    end_clock: str,
) -> bool:
    """Check if period+clock falls within a drive's start→end range.

    NFL clock counts down, so start_clock > end_clock within a quarter.
    """
    play_remaining = game_clock_to_remaining(period, clock)
    start_remaining = game_clock_to_remaining(start_period, start_clock)
    end_remaining = game_clock_to_remaining(end_period, end_clock)
    return end_remaining <= play_remaining <= start_remaining


def compute_win_probability(
    home_score: int, away_score: int, period: int, clock: str
) -> float:
    """Compute home team win probability from score and time remaining.

    Simple logistic model: score differential matters more as time runs out.
    """
    time_remaining = game_clock_to_remaining(period, clock)
    total_time = 4 * QUARTER_SECONDS  # 3600 seconds
    time_fraction = time_remaining / total_time if total_time > 0 else 0.0

    score_diff = home_score - away_score

    # Score matters more as game progresses; at game start it barely matters
    weight = 1.0 + 2.0 * (1.0 - time_fraction)
    logit = score_diff * weight * 0.2

    return 1.0 / (1.0 + math.exp(-logit))


def make_odds_event(
    game_id: str,
    sport: str,
    home_tricode: str,
    away_tricode: str,
    home_win_prob: float,
    ts: str,
) -> dict[str, Any]:
    """Create an OddsUpdateEvent dict with simulated moneyline odds."""
    # Clamp probabilities to avoid division by zero
    home_prob = max(0.01, min(0.99, home_win_prob))
    away_prob = 1.0 - home_prob

    event = OddsUpdateEvent(
        timestamp=datetime.fromisoformat(ts),
        game_id=game_id,
        sport=sport,
        home_tricode=home_tricode,
        away_tricode=away_tricode,
        odds=OddsInfo(
            provider="simulated",
            moneyline=MoneylineOdds(
                home_probability=round(home_prob, 4),
                away_probability=round(away_prob, 4),
                home_odds=round(1.0 / home_prob, 2),
                away_odds=round(1.0 / away_prob, 2),
            ),
        ),
    )
    return event.to_dict()


# ─── Main Processing ────────────────────────────────────────────────────────


def cook_jsonl(input_path: Path, output_path: Path) -> None:
    """Process a raw NFL JSONL into a cleaned, interleaved version."""

    # ── Step 1: Load and classify events ─────────────────────────────────
    logger.info("Loading events from %s", input_path)

    game_init: dict[str, Any] | None = None
    game_start: dict[str, Any] | None = None
    game_result: dict[str, Any] | None = None
    game_update_template: dict[str, Any] | None = (
        None  # Use as template for generated updates
    )
    pregame_events: list[dict[str, Any]] = []
    drives: list[dict[str, Any]] = []
    plays: list[dict[str, Any]] = []
    raw_odds: list[dict[str, Any]] = []

    with open(input_path) as f:
        for line in f:
            if not line.strip():
                continue
            event = json.loads(line)
            etype = event.get("event_type", "")

            if etype == "event.game_initialize":
                game_init = event
            elif etype == "event.game_start":
                game_start = event
            elif etype == "event.game_result":
                game_result = event
            elif etype == "event.nfl_game_update":
                game_update_template = event  # Keep as template
            elif etype == "event.nfl_drive":
                drives.append(event)
            elif etype == "event.nfl_play":
                plays.append(event)
            elif etype == "event.odds_update":
                raw_odds.append(event)
            elif etype in PREGAME_EVENT_TYPES:
                pregame_events.append(event)
            else:
                # Unknown event types go to pregame bucket
                pregame_events.append(event)

    if not game_init:
        logger.error("No game_initialize event found, cannot process")
        return

    game_time_str = game_init.get("game_time", "")
    if not game_time_str:
        logger.error("game_initialize has no game_time field")
        return

    game_time = datetime.fromisoformat(game_time_str)
    game_id = game_init.get("game_id", "")
    sport = game_init.get("sport", "nfl")

    # Extract team info
    home_team = game_init.get("home_team", {})
    away_team = game_init.get("away_team", {})
    home_tricode = home_team.get("tricode", "")
    away_tricode = away_team.get("tricode", "")

    logger.info(
        "Game %s: %s @ %s on %s (%d plays, %d drives)",
        game_id,
        away_tricode,
        home_tricode,
        game_time.isoformat(),
        len(plays),
        len(drives),
    )

    # ── Step 2: Build play timeline ──────────────────────────────────────

    # Sort plays by sequence_number (authoritative ordering from ESPN)
    plays.sort(key=lambda p: int(p.get("sequence_number", 0) or 0))

    # Always recompute game_timestamp from period+clock using 3.25x multiplier.
    # Raw file values may have been computed with incorrect multiplier.
    for play in plays:
        period = int(play.get("period", 0) or 0)
        clock = play.get("clock", "")
        if period >= 1:
            gt = compute_game_timestamp(game_time, period, clock)
            play["game_timestamp"] = gt.isoformat()

    first_play_ts = plays[0]["game_timestamp"] if plays else game_time.isoformat()
    last_play_ts = plays[-1]["game_timestamp"] if plays else game_time.isoformat()

    # ── Step 3: Assign game_timestamp to drives ──────────────────────────

    for drive in drives:
        start_period = int(drive.get("start_period", 0) or 0)
        start_clock = drive.get("start_clock", "")
        end_period = int(drive.get("end_period", 0) or 0)
        end_clock = drive.get("end_clock", "")

        # Find the last play in this drive's time range
        best_play_ts: str | None = None
        for play in plays:
            p_period = int(play.get("period", 0) or 0)
            p_clock = play.get("clock", "")
            if p_period >= 1 and start_period >= 1 and end_period >= 1:
                if is_clock_in_range(
                    p_period, p_clock, start_period, start_clock, end_period, end_clock
                ):
                    best_play_ts = play.get("game_timestamp")

        if best_play_ts:
            # Place drive slightly after its last play (1 second)
            dt = datetime.fromisoformat(best_play_ts) + timedelta(seconds=1)
            drive["game_timestamp"] = dt.isoformat()
        else:
            # No matching plays in range — find nearest play by game clock proximity
            target_remaining = game_clock_to_remaining(
                end_period or start_period, end_clock or start_clock
            )
            nearest_ts = last_play_ts
            nearest_dist = float("inf")
            for play in plays:
                p_period = int(play.get("period", 0) or 0)
                p_clock = play.get("clock", "")
                if p_period >= 1:
                    dist = abs(
                        game_clock_to_remaining(p_period, p_clock) - target_remaining
                    )
                    if dist < nearest_dist:
                        nearest_dist = dist
                        nearest_ts = play.get("game_timestamp", last_play_ts)
            dt = datetime.fromisoformat(nearest_ts) + timedelta(seconds=1)
            drive["game_timestamp"] = dt.isoformat()

    # ── Step 4: Generate game_update events after scoring plays ──────────
    #
    # For backtesting, we emit an NFLGameUpdateEvent after each scoring play
    # so agents receive periodic game state snapshots throughout the game.

    game_updates: list[dict[str, Any]] = []
    for play in plays:
        if not play.get("is_scoring_play"):
            continue

        play_ts = play.get("game_timestamp", "")
        if not play_ts:
            continue

        home_score = int(play.get("home_score", 0) or 0)
        away_score = int(play.get("away_score", 0) or 0)
        period = int(play.get("period", 0) or 0)
        clock = play.get("clock", "")
        down = int(play.get("down", 0) or 0)
        distance = int(play.get("distance", 0) or 0)
        yard_line = play.get("yard_line", 0)

        # Create game_update event based on template or minimal structure
        update_event: dict[str, Any] = {
            "event_type": "event.nfl_game_update",
            "game_id": game_id,
            "sport": sport,
            "period": period,
            "game_clock": clock,
            "home_score": home_score,
            "away_score": away_score,
            "possession": play.get("team_tricode", ""),
            "down": down,
            "distance": distance,
            "yard_line": yard_line,
            "home_team_stats": {},
            "away_team_stats": {},
            "home_line_scores": [],
            "away_line_scores": [],
        }

        # Copy additional fields from template if available
        if game_update_template:
            for key in ("home_team_stats", "away_team_stats"):
                if key in game_update_template:
                    # Deep copy to avoid mutating template
                    update_event[key] = copy.deepcopy(game_update_template[key])

        # Override nested score fields with correct score at this moment
        if update_event["home_team_stats"]:
            update_event["home_team_stats"]["score"] = home_score
        if update_event["away_team_stats"]:
            update_event["away_team_stats"]["score"] = away_score

        # Set timestamp slightly after the scoring play (0.5 second)
        dt = datetime.fromisoformat(play_ts) + timedelta(seconds=0.5)
        update_event["timestamp"] = dt.isoformat()
        update_event["game_timestamp"] = dt.isoformat()

        game_updates.append(update_event)

    logger.info(
        "Generated %d game_update events after scoring plays", len(game_updates)
    )

    # ── Step 5: Interleave drives, plays, and game_updates ─────────────────

    in_game_events: list[dict[str, Any]] = []
    in_game_events.extend(plays)
    in_game_events.extend(drives)
    in_game_events.extend(game_updates)

    # ── Step 6: Fix lifecycle event timestamps ───────────────────────────

    game_time_iso = game_time.isoformat()

    # game_initialize: timestamp = game_time - 60min
    game_init["timestamp"] = (game_time - timedelta(minutes=60)).isoformat()
    game_init["game_timestamp"] = game_time_iso

    # Pregame events: spread between game_time - 30min and game_time - 5min
    if pregame_events:
        pregame_start = game_time - timedelta(minutes=30)
        pregame_end = game_time - timedelta(minutes=5)
        n = len(pregame_events)
        for i, evt in enumerate(pregame_events):
            if n > 1:
                frac = i / (n - 1)
            else:
                frac = 0.5
            ts = pregame_start + (pregame_end - pregame_start) * frac
            evt["timestamp"] = ts.isoformat()
            evt["game_timestamp"] = None

    # game_start
    if game_start:
        game_start["timestamp"] = first_play_ts
        game_start["game_timestamp"] = first_play_ts

    # game_updates are already generated and timestamped in Step 4

    # game_result
    if game_result:
        game_result["timestamp"] = last_play_ts
        game_result["game_timestamp"] = last_play_ts

    # ── Step 7: Simulate moneyline odds (every 5 seconds) ───────────────

    odds_events: list[dict[str, Any]] = []
    ODDS_INTERVAL_SECONDS = 5  # Match real Polymarket in-game polling

    # Pre-game odds: at game_time - 5min, use 50/50 as default
    pregame_odds_ts = (game_time - timedelta(minutes=5)).isoformat()
    # Try to get initial probability from existing odds
    # Skip post-game odds where probability is 0 or 1 (settled market)
    initial_home_prob = 0.5
    if raw_odds:
        odds_data = raw_odds[0].get("odds", {})
        ml = odds_data.get("moneyline", {})
        if ml:
            hp = ml.get("home_probability", 0)
            if 0.01 < hp < 0.99:
                initial_home_prob = hp

    odds_events.append(
        make_odds_event(
            game_id,
            sport,
            home_tricode,
            away_tricode,
            initial_home_prob,
            pregame_odds_ts,
        )
    )

    # Build scoring timeline: list of (game_timestamp, home_score, away_score)
    # so we can track current score at any point during the game
    scoring_timeline: list[tuple[datetime, int, int]] = []
    for play in plays:
        if not play.get("is_scoring_play"):
            continue
        play_ts = play.get("game_timestamp", "")
        if not play_ts:
            continue
        home_score = int(play.get("home_score", 0) or 0)
        away_score = int(play.get("away_score", 0) or 0)
        scoring_timeline.append(
            (datetime.fromisoformat(play_ts), home_score, away_score)
        )
    scoring_timeline.sort(key=lambda x: x[0])

    # In-game odds: emit every 5 seconds from first play to last play
    if plays:
        game_start_ts = datetime.fromisoformat(first_play_ts)
        game_end_ts = datetime.fromisoformat(last_play_ts)
        game_duration = (game_end_ts - game_start_ts).total_seconds()

        # Track current score by walking through scoring timeline
        score_idx = 0
        current_home_score = 0
        current_away_score = 0

        tick = 0.0
        while tick <= game_duration:
            tick_ts = game_start_ts + timedelta(seconds=tick)

            # Advance score to this point in time
            while score_idx < len(scoring_timeline):
                score_time, h_score, a_score = scoring_timeline[score_idx]
                if score_time <= tick_ts:
                    current_home_score = h_score
                    current_away_score = a_score
                    score_idx += 1
                else:
                    break

            # Estimate period and clock from wallclock elapsed time
            elapsed = tick
            # Reverse the game_timestamp computation to get approximate period+clock
            # Walk through quarters to find which one we're in
            period = 1
            clock_remaining = QUARTER_SECONDS
            for q in range(1, 6):
                q_start = QUARTER_OFFSETS.get(q, QUARTER_OFFSETS[5])
                q_end = QUARTER_OFFSETS.get(
                    q + 1, q_start + int(QUARTER_SECONDS * GAME_CLOCK_MULTIPLIER)
                )
                if elapsed < q_end:
                    period = q
                    elapsed_in_q = max(0, elapsed - q_start)
                    game_seconds_elapsed = elapsed_in_q / GAME_CLOCK_MULTIPLIER
                    clock_remaining = max(0, QUARTER_SECONDS - game_seconds_elapsed)
                    break

            minutes = int(clock_remaining) // 60
            seconds = int(clock_remaining) % 60
            clock_str = f"{minutes}:{seconds:02d}"

            prob = compute_win_probability(
                current_home_score, current_away_score, period, clock_str
            )

            odds_ts = tick_ts.isoformat()
            odds_events.append(
                make_odds_event(
                    game_id, sport, home_tricode, away_tricode, prob, odds_ts
                )
            )

            tick += ODDS_INTERVAL_SECONDS

    # Add odds to in-game events
    in_game_events.extend(odds_events)

    # ── Step 8: Sort in-game events and set timestamp = game_timestamp ───

    def sort_key(e: dict[str, Any]) -> str:
        gt = e.get("game_timestamp") or e.get("timestamp") or ""
        return gt

    in_game_events.sort(key=sort_key)

    # Set timestamp = game_timestamp for all in-game events
    for evt in in_game_events:
        gt = evt.get("game_timestamp")
        if gt:
            evt["timestamp"] = gt

    # ── Assemble final output ────────────────────────────────────────────

    output_events: list[dict[str, Any]] = []

    # 1. game_initialize
    output_events.append(game_init)

    # 2. Pregame events
    pregame_events.sort(key=lambda e: e.get("timestamp", ""))
    output_events.extend(pregame_events)

    # 3. Pre-game odds (already in odds_events[0], placed in pregame section)
    # Actually odds_events[0] is pre-game, rest are in-game. Split them.
    if odds_events:
        pregame_odds = odds_events[0]
        output_events.append(pregame_odds)
        # Remove pregame odds from in_game_events
        in_game_events = [
            e
            for e in in_game_events
            if not (
                e.get("event_type") == "event.odds_update"
                and e.get("timestamp") == pregame_odds.get("timestamp")
            )
        ]

    # 4. game_start
    if game_start:
        output_events.append(game_start)

    # 5. In-game events (plays, drives, game_updates, odds interleaved)
    output_events.extend(in_game_events)

    # 6. game_result (game_updates are now interspersed with plays)
    if game_result:
        output_events.append(game_result)

    # ── Write output ─────────────────────────────────────────────────────

    logger.info("Writing %d events to %s", len(output_events), output_path)
    with open(output_path, "w") as f:
        for event in output_events:
            f.write(json.dumps(event, default=str) + "\n")

    # ── Summary ──────────────────────────────────────────────────────────

    event_counts: dict[str, int] = {}
    for e in output_events:
        etype = e.get("event_type", "unknown")
        event_counts[etype] = event_counts.get(etype, 0) + 1

    logger.info("Output summary:")
    for etype, count in sorted(event_counts.items()):
        logger.info("  %s: %d", etype, count)

    # Show odds progression (sample every ~5 minutes of game time)
    odds_in_output = [
        e for e in output_events if e.get("event_type") == "event.odds_update"
    ]
    logger.info("Odds progression (%d total, showing sample):", len(odds_in_output))
    sample_interval = max(1, len(odds_in_output) // 20)  # ~20 samples
    for i, e in enumerate(odds_in_output):
        if i == 0 or i == len(odds_in_output) - 1 or i % sample_interval == 0:
            ml = (e.get("odds") or {}).get("moneyline", {})
            logger.info(
                "  %s  home=%.1f%% (%.2f)  away=%.1f%% (%.2f)",
                (e.get("game_timestamp") or e.get("timestamp") or "")[:19],
                ml.get("home_probability", 0) * 100,
                ml.get("home_odds", 0),
                ml.get("away_probability", 0) * 100,
                ml.get("away_odds", 0),
            )


# ─── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post-process a raw NFL JSONL for proper replay."
    )
    parser.add_argument("input", type=str, help="Path to input JSONL file")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to output JSONL file (default: input with -cooked suffix)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        return 1

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_stem(input_path.stem + "-cooked")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cook_jsonl(input_path, output_path)
    plot_odds(output_path)

    logger.info("Done: %s", output_path)
    return 0


def plot_odds(output_path: Path) -> None:
    """Generate odds progression plot from cooked JSONL."""
    timestamps: list[datetime] = []
    home_probs: list[float] = []
    scoring_times: list[datetime] = []
    scoring_labels: list[str] = []
    home_tricode = "HOME"
    away_tricode = "AWAY"
    game_date = ""

    with open(output_path) as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            etype = e.get("event_type", "")

            if etype == "event.game_initialize":
                ht = e.get("home_team", {})
                at = e.get("away_team", {})
                if isinstance(ht, dict):
                    home_tricode = ht.get("tricode") or ht.get("abbrev") or "HOME"
                if isinstance(at, dict):
                    away_tricode = at.get("tricode") or at.get("abbrev") or "AWAY"
                gt = e.get("game_time", "")
                if gt:
                    game_date = gt[:10]

            elif etype == "event.odds_update":
                ts = e.get("game_timestamp") or e.get("timestamp")
                if ts:
                    t = datetime.fromisoformat(str(ts))
                    ml = (e.get("odds") or {}).get("moneyline", {})
                    hp = ml.get("home_probability", 0)
                    timestamps.append(t)
                    home_probs.append(hp * 100)

            elif etype == "event.nfl_play" and e.get("is_scoring_play"):
                ts = e.get("game_timestamp")
                if ts:
                    t = datetime.fromisoformat(str(ts))
                    hs = e.get("home_score", 0)
                    aws = e.get("away_score", 0)
                    scoring_times.append(t)
                    scoring_labels.append(f"{hs}-{aws}")

    if not timestamps:
        logger.warning("No odds events found, skipping plot")
        return

    # Convert datetimes to matplotlib numeric format for type safety
    ts_num = mdates.date2num(timestamps)
    score_num = mdates.date2num(scoring_times) if scoring_times else []

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.fill_between(
        ts_num,
        home_probs,
        50,
        where=[p >= 50 for p in home_probs],
        alpha=0.15,
        color="#1f77b4",
        interpolate=True,
    )
    ax.fill_between(
        ts_num,
        home_probs,
        50,
        where=[p < 50 for p in home_probs],
        alpha=0.15,
        color="#d62728",
        interpolate=True,
    )
    ax.plot(
        ts_num,
        home_probs,
        color="#1f77b4",
        linewidth=1.5,
        label=f"{home_tricode} (home) win %",
    )
    ax.axhline(y=50, color="gray", linestyle="--", alpha=0.5, linewidth=0.8)

    for t_num, label in zip(score_num, scoring_labels):
        ax.axvline(x=t_num, color="green", alpha=0.3, linewidth=0.8)
        ax.annotate(
            label,
            xy=(t_num, 93),
            fontsize=7,
            ha="center",
            color="#2ca02c",
            rotation=45,
        )

    n_odds = len(timestamps)
    title = (
        f"Simulated Moneyline Odds: {away_tricode} @ {home_tricode}"
        f" ({game_date})  |  {n_odds:,} events @ 5s intervals"
    )
    ax.set_xlabel("Game Time (UTC)")
    ax.set_ylabel("Home Win Probability (%)")
    ax.set_title(title)
    ax.set_ylim(0, 100)
    ax.legend(loc="lower right")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    plot_path = output_path.with_suffix(".png")
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)
    logger.info("Odds plot saved: %s", plot_path)


if __name__ == "__main__":
    sys.exit(main())
