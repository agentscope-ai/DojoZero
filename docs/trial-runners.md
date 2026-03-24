# Tool trial runners (NBA & NFL)

Standalone Python tools under `tools/` **discover upcoming or in-progress games** from each league’s data source, **materialize per-game trial configs**, and **launch DojoZero trials** (local subprocess or Dashboard Server). They poll until the game ends, persist **JSONL event logs** for replay, and support the same **`.env` credentials** as the rest of the stack.

| | **NBA** | **NFL** |
|---|---------|---------|
| **Script** | `tools/nba_trial_runner.py` | `tools/nfl_trial_runner.py` |
| **Schedule source** | NBA API (game list by date) | ESPN API (scoreboard by date or week) |
| **Default params template** | `trial_params/nba-moneyline.yaml` | `trial_params/nfl-moneyline.yaml` |
| **Game ID** | NBA `gameId` | ESPN `eventId` (aliases: `--game-id` / `--event-id`) |
| **Extra selectors** | Date range for `list` | `--week`, `--season-type` for `list` / `run` |

Use these when you want **CLI-driven, game-centric batching** instead of hand-picking params files or relying only on `dojo0 serve` trial sources. Orchestration patterns (server mode, scheduling) are covered in [`docs/cli.md`](./cli.md).

## Quick start

Run from the **repository root** so imports and default paths resolve.

### NBA

```bash
# Today’s games
python tools/nba_trial_runner.py run

# Specific date or single game
python tools/nba_trial_runner.py run --date 2025-01-12
python tools/nba_trial_runner.py run --game-id 0022400123

# List games in a range
python tools/nba_trial_runner.py list
python tools/nba_trial_runner.py list --start-date 2025-01-12 --end-date 2025-01-19
```

### NFL

```bash
# Today’s games
python tools/nfl_trial_runner.py run

# By date, week, or single ESPN event
python tools/nfl_trial_runner.py run --date 2025-01-12
python tools/nfl_trial_runner.py run --week 18
python tools/nfl_trial_runner.py run --game-id 401772976
# alias:
python tools/nfl_trial_runner.py run --event-id 401772976

# List games
python tools/nfl_trial_runner.py list
python tools/nfl_trial_runner.py list --start-date 2025-01-12 --end-date 2025-01-19
python tools/nfl_trial_runner.py list --week 18
```

## Shared behavior

- **Per-game configs**: Each game gets its own generated YAML (and matching JSONL path) under your output layout.
- **Start timing**: Trials typically start a short time before tipoff/kickoff (`--pre-start-hours`, default `0.1`).
- **Polling**: Game status is checked on an interval (`--check-interval`, default `60` seconds) until the game completes.
- **Dashboard Server**: Pass `--server http://localhost:8000` (after `dojo0 serve …`) so trials are submitted to the server and tracing can flow through the dashboard stack (see [`docs/tracing.md`](./tracing.md)).
- **Concurrency**: `--max-concurrent-starts` limits parallel trial startups when many games match.

## Configuration and environment

- **Credentials**: API/model keys live in `.env` (see [`docs/configuration.md`](./configuration.md)).
- **Custom trial template**: `--config /path/to/template.yaml` overrides the default moneyline templates.
- **Output root**: `--data-dir` stores artifacts as `{data-dir}/{date}/{game_or_event_id}.yaml` and `.jsonl` (when set; otherwise the tools fall back to their built-in defaults — see `--help`).

### Config shape (both sports)

Templates use the shared trial-params layout: `scenario.name` is `nba` or `nfl`, with `espn_game_id` (or equivalent game key) and `hub.persistence_file` for JSONL. Example skeletons:

**NBA** (`scenario.name: nba`):

```yaml
scenario:
  name: nba
  config:
    espn_game_id: "0022400123"
    hub:
      persistence_file: outputs/nba_betting_events-{espn_game_id}.jsonl
    # data_streams / operators / agents …
```

**NFL** (`scenario.name: nfl`):

```yaml
scenario:
  name: nfl
  config:
    espn_game_id: "401772976"
    hub:
      persistence_file: outputs/nfl_betting_events-{espn_game_id}.jsonl
    # data_streams / operators / agents …
```

Extend `data_streams` / agents in the template the same way you would for any trial params file.

## Event types (reference)

Trial params list **stream subscription names** (e.g. `nfl_play`, `nba_play`, `odds_update`). The exact persisted `event_type` strings in JSONL follow the registered data events in `src/dojozero/data/` (NFL/NBA modules and shared lifecycle models). Use `trial_params/nba-moneyline.yaml` and `trial_params/nfl-moneyline.yaml` as the source of truth for which streams a moneyline trial wires up.

## CLI options

Run `python tools/nba_trial_runner.py run --help` or `python tools/nfl_trial_runner.py run --help` for the exact list on your checkout.

**Common (both runners)**

| Option | Purpose |
|--------|---------|
| `--date` | Run for a calendar day (default: today) |
| `--game-id` | Single game (NFL: same as `--event-id`) |
| `--config` | Params template path |
| `--data-dir` | Output directory root |
| `--pre-start-hours` | How long before start to launch |
| `--check-interval` | Status poll interval (seconds) |
| `--log-level` | `DEBUG` … `ERROR` |
| `--server` | Dashboard Server base URL |
| `--max-concurrent-starts` | Parallel trial submissions cap |

**NFL-only**

| Option | Purpose |
|--------|---------|
| `--week` | Week-based discovery (with `--season-type`) |
| `--season-type` | `1` preseason, `2` regular, `3` postseason |
| `--trial-id` | Override auto-generated trial id |

**NBA `list`**

- `--start-date` / `--end-date` — date range (defaults: today).

**NFL `list`**

- Same date range, plus `--week` and `--season-type`.

## Backtest from captured JSONL

After a run, replay with `dojo0 backtest` using the written JSONL and the generated params for that game:

```bash
uv run dojo0 backtest \
  --events outputs/2026-01-12/401772976.jsonl \
  --params outputs/2026-01-12/401772976.yaml \
  --speed 100 \
  --max-sleep 1
```

More examples: [`docs/backtesting.md`](./backtesting.md).

## Output

Events are appended in **JSONL** (one JSON object per line). Typical layout:

```text
{data-dir}/{YYYY-MM-DD}/{game_or_event_id}.jsonl
{data-dir}/{YYYY-MM-DD}/{game_or_event_id}.yaml
```

## See also

- [`README.md`](../README.md) — high-level quick start
- [`docs/cli.md`](./cli.md) — `dojo0 run`, `serve`, scheduling
- [`docs/configuration.md`](./configuration.md) — environment and trial config
