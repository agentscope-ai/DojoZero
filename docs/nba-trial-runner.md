# NBA Trial Runner

Automated driver for collecting NBA game replay data. Schedules and runs trials for daily games, starting 2 hours before tipoff and stopping when games conclude.

## Commands

### List Games

```bash
# List today's games
python tools/nba_trial_runner.py list

# List games for a specific date
python tools/nba_trial_runner.py list --start-date 2025-12-16

# List games for a date range
python tools/nba_trial_runner.py list --start-date 2025-12-10 --end-date 2025-12-16
```

### Collect Data

```bash
# Collect today's games
python tools/nba_trial_runner.py collect --data-dir data/nba-betting

# Collect for a specific date
python tools/nba_trial_runner.py collect --data-dir data/nba-betting --date 2025-12-16

# Collect specific game
python tools/nba_trial_runner.py collect --data-dir data/nba-betting --game-id 0062500001
```

**Options:**
- `--data-dir`: Output directory (`{data-dir}/{date}/{game_id}.{yaml,jsonl,log}`)
- `--date`: Date to collect (YYYY-MM-DD, default: today)
- `--game-id`: Specific game ID (optional)
- `--base-config`: Config template (default: `configs/nba-pregame-betting.yaml`)
- `--pre-start-hours`: Hours before game to start (default: 2.0)
- `--check-interval`: Status check interval in seconds (default: 60.0)
- `--log-level`: DEBUG, INFO, WARNING, ERROR (default: INFO)
- `--oss-upload`: Enable OSS upload after collection
- `--oss-bucket`: Override OSS bucket
- `--oss-prefix`: Override OSS prefix

## Output Structure

```
data/nba-betting/2025-12-16/
  ├── 0062500001.yaml    # Trial config
  ├── 0062500001.jsonl   # Replay events
  └── 0062500001.log     # Trial logs
```

## How It Works

1. Fetches games from NBA API for the date
2. Generates per-game configs in `{data-dir}/{date}/`
3. Schedules trials to start 2 hours before tipoff
4. Launches `dojozero run` subprocess for each game
5. Monitors game status every 60 seconds
6. Stops trial when game status = 3 (Finished)

## Replay

```bash
dojozero replay \
  --replay-file data/nba-betting/2025-12-16/0062500001.jsonl \
  --params data/nba-betting/2025-12-16/0062500001.yaml \
  --replay-speed-up 2.0
```

## Notes

- Runs until all games complete (Ctrl+C to stop early)
- Each game runs in a separate subprocess
- Log files capture all subprocess output
