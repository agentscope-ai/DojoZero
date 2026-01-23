# NFL Trial Runner

Orchestrates betting trials for NFL games using ESPN API data including plays, drives, scores, and odds.

## Quick Start

### Run Trial for Today's Games

```bash
python tools/nfl_trial_runner.py run
```

### Run Trial for Specific Date

```bash
python tools/nfl_trial_runner.py run --date 2025-01-12
```

### Run Trial for Specific Week

```bash
python tools/nfl_trial_runner.py run --week 18
```

### Run Trial for Specific Event

```bash
python tools/nfl_trial_runner.py run --event-id 401772976
```

### List Games

```bash
python tools/nfl_trial_runner.py list --date 2025-01-12
python tools/nfl_trial_runner.py list --week 18
```

## Configuration

### Daily Runner

```bash
# Run NFL trials using the daily runner script
python deploy/run_daily_trials.py configs/nfl-game.yaml
python deploy/run_daily_trials.py configs/nfl-game.yaml --date 2025-01-12
```

### Environment Variables

- `DATA_DIR` - Output directory for trial data (default: `data/nfl`)
- `OSS_UPLOAD` - Set to "true" to enable OSS upload

### Custom Config

```bash
python tools/nfl_trial_runner.py run --config /path/to/custom-config.yaml
```

### Config File Structure

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

## CLI Options

```
python tools/nfl_trial_runner.py run --help

Options:
  --date DATE           Date to run trials for (YYYY-MM-DD)
  --week WEEK           NFL week number (1-18 for regular season)
  --season-type {1,2,3} 1=preseason, 2=regular, 3=postseason
  --event-id EVENT_ID   Specific ESPN event ID
  --config CONFIG       Path to trial config template
  --data-dir DATA_DIR   Data directory for output
  --pre-start-hours     Hours before kickoff to start (default: 1.0)
  --check-interval      Status check interval in seconds (default: 60.0)
  --log-level           DEBUG, INFO, WARNING, ERROR
  --oss-upload          Upload files to OSS after completion
  --oss-bucket          Override OSS bucket name
  --oss-prefix          Override OSS prefix
```

## Backtest from File

```bash
uv run dojo0 backtest \
  --events outputs/2026-01-12/401772976.jsonl \
  --params outputs/2026-01-12/401772976.yaml \
  --speed 100 \
  --max-sleep 1
```

## Output

Events are persisted to JSONL format:

```json
{"event_type": "nfl_play", "timestamp": "2026-01-13T16:54:01Z", "event_id": "401772976", ...}
```

Files are saved to `{data-dir}/{date}/{event_id}.jsonl`.
