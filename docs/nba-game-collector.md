# NBA Game Collector

Automated driver for collecting replay data for NBA games. Orchestrates data collection by checking NBA API for daily games, setting up separate trials for each game, starting trials 2 hours before game time, and running until games conclude.

## Commands

### List Games

List games for a date range (defaults to today):

```bash
# List today's games
python tools/nba_game_collector.py list

# List games for a specific date
python tools/nba_game_collector.py list --start-date 2025-12-16

# List games for a date range
python tools/nba_game_collector.py list --start-date 2025-12-10 --end-date 2025-12-16
```

**List options:**
- `--start-date`: Start date (YYYY-MM-DD). Default: today
- `--end-date`: End date (YYYY-MM-DD). Default: same as start date
- `--log-level`: Logging level (default: WARNING)

### Collect Data

Collect game data for a date:

```bash
# Collect today's games
python tools/nba_game_collector.py collect --data-dir data/nba-betting

# Collect for a specific date
python tools/nba_game_collector.py collect --data-dir data/nba-betting --date 2025-12-16
```

**Collect options:**
- `--data-dir`: Data directory where all files are organized: `{data-dir}/{date}/{game_id}.{yaml,jsonl,log}`
- `--date`: Date to collect games for (YYYY-MM-DD). Default: today
- `--game-id`: Specific game ID to collect (optional)
- `--base-config`: Base config template (default: `configs/nba-pregame-betting.yaml`)
- `--pre-start-hours`: Hours before game to start trial (default: 2.0)
- `--check-interval`: Seconds between game status checks (default: 60.0)
- `--log-level`: Logging level: DEBUG, INFO, WARNING, ERROR (default: INFO)
- `--oss-upload`: Enable OSS upload after collection
- `--oss-bucket`: Override OSS bucket name
- `--oss-prefix`: Override OSS prefix

**Debug logging:**
```bash
python tools/nba_game_collector.py collect --data-dir data/nba-betting --log-level DEBUG
```

## File Structure

With `--data-dir data/nba-betting` and `--date 2025-12-16`:
```
data/nba-betting/2025-12-16/
  ├── 0062500001.yaml    # Config
  ├── 0062500001.jsonl   # Replay events
  └── 0062500001.log     # Trial logs (stdout/stderr)
```

**All files (configs, replays, logs) go to the same directory** when using `--data-dir`.

## How It Works

1. Fetches games from NBA API for the specified date
2. Generates per-game config files in `{data-dir}/{date}/{game_id}.yaml`
3. Schedules trials to start 2 hours before each game
4. Launches `dojo0 run` process for each game (isolated subprocess)
5. Monitors game status every 60 seconds
6. Stops trial when game status = 3 (Finished)
7. Logs all events to `{data-dir}/{date}/{game_id}.log`

## Replay

After collection, replay a game:
```bash
dojo0 replay \
  --replay-file data/nba-betting/2025-12-16/0062500001.jsonl \
  --params data/nba-betting/2025-12-16/0062500001.yaml \
  --replay-speed-up 2.0
```

## Notes

- Script runs until all games complete (use Ctrl+C to stop)
- Each game runs in a separate process with isolated logging
- Log files capture all subprocess output including `logger.info()` calls
- Trials automatically stop when games finish
