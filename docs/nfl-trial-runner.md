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
python tools/nfl_trial_runner.py run --game-id 401772976
# alias also supported:
python tools/nfl_trial_runner.py run --event-id 401772976
```

### List Games

```bash
python tools/nfl_trial_runner.py list
python tools/nfl_trial_runner.py list --start-date 2025-01-12
python tools/nfl_trial_runner.py list --start-date 2025-01-12 --end-date 2025-01-19
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

- Runner-specific output is controlled with `--data-dir`.
- API/model credentials are configured in `.env` (see [`docs/configuration.md`](./configuration.md)).

### Custom Config

```bash
python tools/nfl_trial_runner.py run --config /path/to/custom-config.yaml
```

### Config File Structure

```yaml
scenario:
  name: nfl
  config:
    espn_game_id: "401772976"  # ESPN event ID
    hub:
      persistence_file: outputs/nfl_betting_events-{espn_game_id}.jsonl
```

### Optional Parameters

```yaml
scenario:
  name: nfl
  config:
    espn_game_id: "401772976"
    hub:
      persistence_file: outputs/nfl_betting_events-{espn_game_id}.jsonl
    # Custom streams/operators/agents can be defined as needed.
    data_streams:
      - id: pre_game_insights_stream
        event_types:
          - injury_report
          - power_ranking
          - expert_prediction
          - pregame_stats
          - twitter_top_tweets
      - id: game_lifecycle_stream
        event_types:
          - game_initialize
          - game_start
          - game_result
      - id: nfl_game_update_stream
        event_types:
          - nfl_game_update
      - id: nfl_odds_update_stream
        event_types:
          - odds_update
      - id: nfl_play_stream
        event_types:
          - nfl_play
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
  --season-type {1,2,3} Season type: 1=preseason, 2=regular, 3=postseason
  --game-id/--event-id  Specific ESPN game/event ID
  --config CONFIG       Path to trial config template
  --data-dir DATA_DIR   Data directory for output
  --pre-start-hours     Hours before kickoff to start (default: 0.1)
  --check-interval      Status check interval in seconds (default: 60.0)
  --log-level           DEBUG, INFO, WARNING, ERROR
  --server              Dashboard Server URL for server mode
  --max-concurrent-starts  Max trial startups in parallel (default: 10)
  --trial-id            Custom trial ID override
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

## See Also

- [`README.md`](../README.md) for concise runner quick start
- [`docs/running-trials.md`](./running-trials.md) for trial orchestration workflows
