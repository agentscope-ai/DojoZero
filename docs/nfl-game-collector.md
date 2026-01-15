# NFL Game Data Collector

Collects real-time NFL game data from ESPN API including plays, drives, scores, and odds.

## Quick Start

### Live Collection

```bash
uv run dojo0 run --params configs/nfl-game.yaml
```

### Replay from File

```bash
uv run dojo0 replay \
  --replay-file outputs/2026-01-12/401772976.jsonl \
  --params outputs/2026-01-12/401772976.yaml \
  --replay-speed-up 100 \
  --replay-max-sleep 1
```

## Configuration

Create a YAML file with the following structure:

```yaml
scenario:
  name: nfl-game
  config:
    event_id: '401772976'  # ESPN event ID
    hub:
      persistence_file: outputs/nfl_events.jsonl
      enable_persistence: true
```

### Optional Parameters

```yaml
scenario:
  name: nfl-game
  config:
    event_id: '401772976'
    hub:
      persistence_file: outputs/nfl_events.jsonl
      enable_persistence: true
    # Polling intervals (seconds)
    scoreboard_poll_interval: 60
    summary_poll_interval: 30
    plays_poll_interval: 10
    # Custom data streams (defaults to all if omitted)
    data_streams:
      - id: nfl_play_stream
        event_type: nfl_play
      - id: nfl_drive_stream
        event_type: nfl_drive
```

## Event Types

| Event Type | Description |
|------------|-------------|
| `nfl_game_initialize` | Game metadata (teams, venue, time) |
| `nfl_game_start` | Game kickoff |
| `nfl_game_result` | Final score and winner |
| `nfl_game_update` | Score updates, team statistics |
| `nfl_play` | Individual plays |
| `nfl_drive` | Drive summaries |
| `nfl_odds_update` | Betting odds from ESPN sportsbook |

## Replay Options

| Option | Default | Description |
|--------|---------|-------------|
| `--replay-speed-up` | 1.0 | Speed multiplier (100 = 100x faster) |
| `--replay-max-sleep` | 20.0 | Max delay between events (seconds) |
| `--trial-id` | random | Custom trial identifier |

## Output

Events are persisted to JSONL format:

```json
{"event_type": "nfl_play", "timestamp": "2026-01-13T16:54:01Z", "event_id": "401772976", ...}
```

Files are saved to the path specified in `hub.persistence_file`.
