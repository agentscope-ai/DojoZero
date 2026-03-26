# Trial Runners (NBA and NFL)

Standalone scripts under `tools/` **discover** upcoming or in-progress games and **run** trials.

| | **NBA** | **NFL** |
|---|---|---|
| **Script** | `tools/nba_trial_runner.py` | `tools/nfl_trial_runner.py` |
| **Default params template** | `trial_params/nba-moneyline.yaml` | `trial_params/nfl-moneyline.yaml` |



## 1. NBA Examples

```bash
# Today's games
python tools/nba_trial_runner.py run

# Specific date or single game
python tools/nba_trial_runner.py run --date 2025-01-12
python tools/nba_trial_runner.py run --game-id 0022400123

# List games in a range
python tools/nba_trial_runner.py list
python tools/nba_trial_runner.py list --start-date 2025-01-12 --end-date 2025-01-19
```

## 2. NFL Examples

```bash
# Today's games
python tools/nfl_trial_runner.py run

# By date, week, or single ESPN event
python tools/nfl_trial_runner.py run --date 2025-01-12
python tools/nfl_trial_runner.py run --week 18
python tools/nfl_trial_runner.py run --game-id 401772976
# Alias:
python tools/nfl_trial_runner.py run --event-id 401772976

# List games
python tools/nfl_trial_runner.py list
python tools/nfl_trial_runner.py list --start-date 2025-01-12 --end-date 2025-01-19
python tools/nfl_trial_runner.py list --week 18
```

## 3. Options

### Shared options (NBA and NFL)

| Option | Purpose |
|---|---|
| `--date` | Run for a calendar day (default: today) |
| `--game-id` | Run a single game/event (`--event-id` is an NFL alias) |
| `--config` | Params template path |
| `--data-dir` | Output directory root |
| `--pre-start-hours` | Launch window before scheduled start |
| `--check-interval` | Poll interval in seconds |
| `--log-level` | Log level (`DEBUG` to `ERROR`) |
| `--server` | Dashboard server base URL |
| `--max-concurrent-starts` | Max parallel trial submissions |

### NFL-only options

| Option | Purpose |
|---|---|
| `--week` | Week-based discovery (with `--season-type`) |
| `--season-type` | `1` preseason, `2` regular, `3` postseason |

## 4. `list` Command Filters

- **NBA:** supports `--start-date` and `--end-date` (default: today).
- **NFL:** supports the same date range options, plus `--week` and `--season-type`.
