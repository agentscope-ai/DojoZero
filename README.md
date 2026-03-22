# DojoZero

DojoZero is a system for hosting AI agents that run continuously on realtime data to reason about future outcomes and act on them, such as placing bets on sports events. DojoZero currently supports NBA and NFL.

## Why Use DojoZero

- Build and evaluate autonomous agents on live, event-driven data streams.
- Compare agent personas and model providers with reproducible trial workflows.
- Run the same scenario in real-time and replay mode (backtesting) for faster iteration.
- Operate as a local CLI workflow or as long-running services with scheduling and tracing.
- Extend scenarios with custom agents, operators, and data streams without changing the core runtime.

## Installation (two options)

| Track | Command | Use when |
|--------|---------|----------|
| **Default (open source core)** | `uv pip install .` | Trials, dashboard, **Jaeger** tracing — no Alibaba Cloud Python SDKs |
| **+ Alibaba / Redis** | `uv pip install '.[alicloud,redis]'` | OSS backup / `oss://` paths, **`--trace-backend sls`**, sync-service **Redis** |

Details, package lists, and dev setup: [`docs/installation.md`](./docs/installation.md).

## Quick Start

Install and run your first trial in a few minutes. The example below uses DashScope-backed models and the **default** install (Jaeger-compatible; no Alibaba wheels):

```bash
# 1) Default install (see table above for [alicloud] / [redis])
uv pip install .

# 2) Set required API keys (example)

export DOJOZERO_DASHSCOPE_API_KEY="your_key"
export DOJOZERO_TAVILY_API_KEY="your_key"

# 3) Run an NBA trial
dojo0 run --params trial_params/nba-moneyline.yaml --trial-id quickstart-nba
```

📘 For the full setup guide, including (1) environment variables, (2) trial configuration, and (3) agent configuration, see [`docs/configuration.md`](./docs/configuration.md). Install options (default vs Alibaba extras) are in [`docs/installation.md`](./docs/installation.md).

## Docker: Single Trial Quick Run

If you only want to run one trial (without starting long-running server mode), use the local-only compose file (separate from `deploy/`):

```bash
# 1) Prepare env
cp .env.example .env
# Fill DOJOZERO_DASHSCOPE_API_KEY and DOJOZERO_TAVILY_API_KEY

# 2) Run one NBA trial
docker compose -f docker/docker-compose.local.yml run --rm trial

# 3) Run one NFL trial (override params + trial id)
DOJOZERO_TRIAL_PARAMS=trial_params/nfl-moneyline.yaml \
DOJOZERO_TRIAL_ID=quickstart-nfl \
docker compose -f docker/docker-compose.local.yml run --rm trial
```

Output files are written to `outputs/` and `data/` on your host machine.

---

🚀 Want automatic scheduling and a web dashboard? Start DojoZero in server mode:

```bash
# Step 1: Install and start Jaeger: [https://www.jaegertracing.io/](https://www.jaegertracing.io/)

# Step2: Start DojoZero dashboard server
dojo0 serve --trace-backend jaeger --trace-ingest-endpoint http://localhost:4318 --trial-source "trial_sources/*.yaml"
```

Then open the Jaeger UI at `http://localhost:16686` to explore traces.
If Jaeger is not installed yet, follow [`docs/tracing.md`](./docs/tracing.md).

## Where To Go Next

- **Run and schedule trials**: [`docs/running-trials.md`](./docs/running-trials.md)
- **Replay historical events**: [`docs/backtesting.md`](./docs/backtesting.md)
- **Understand architecture and design decisions**: [`docs/architecture.md`](./docs/architecture.md)
- **Configure trials and agents**: [`docs/configuration.md`](./docs/configuration.md)


## Trial Runner Tools

DojoZero includes dedicated trial runners for NBA and NFL game scheduling, orchestration, and data capture.

### NBA Trial Runner (`tools/nba_trial_runner.py`)

Automates NBA trial runs: fetches daily games, starts trials before tipoff, and stops them when games finish.

```bash
# List games
python tools/nba_trial_runner.py list
python tools/nba_trial_runner.py list --start-date 2025-12-16
python tools/nba_trial_runner.py list --start-date 2025-12-10 --end-date 2025-12-16

# Run trials
python tools/nba_trial_runner.py run --data-dir data/nba-betting
python tools/nba_trial_runner.py run --data-dir data/nba-betting --date 2025-12-16
python tools/nba_trial_runner.py run --data-dir data/nba-betting --game-id 0062500001
```

Options:
- `--data-dir`: Output directory (`{data-dir}/{date}/{game_id}.{yaml,jsonl,log}`)
- `--date`: Date to run (YYYY-MM-DD, default: today)
- `--game-id`: Run a specific game only
- `--config`: Params template (default: `trial_params/nba-moneyline.yaml`)
- `--pre-start-hours`: Lead time before game start (default: `0.1`)
- `--check-interval`: Poll interval in seconds (default: `60.0`)
- `--log-level`: `DEBUG`, `INFO`, `WARNING`, `ERROR`
- `--server`: Dashboard Server URL for server mode and trace export

Server mode (with trace export):

```bash
# Terminal 1
dojo0 serve --trace-backend jaeger

# Terminal 2
python tools/nba_trial_runner.py run --data-dir data/nba-betting --server http://localhost:8000
```

Output structure:

```text
data/nba-betting/2025-12-16/
  0062500001.yaml
  0062500001.jsonl
  0062500001.log
```

### NFL Trial Runner (`tools/nfl_trial_runner.py`)

This workflow is similar to the NBA trial runner. For complete NFL runner documentation (CLI options, config schema, event types, output layout, and backtest examples), see [`docs/nfl-trial-runner.md`](./docs/nfl-trial-runner.md).

## What to Expect Next

These are some of the efforts we are currently working on:

#### 🧭 1. More Prediction Scenarios

You can start from existing NBA/NFL builders, then extend to:
- More sports and market types (e.g., FIFA soccer markets, spreads, totals, and props).
- Non-sports forecasting domains with event-sourced data streams.
- Custom operators (execution, risk limits, portfolio constraints).


#### 🧠 2. Use RL To Improve Your Agent

You can use backtesting data and broker outcomes to train better betting policies over time

#### 👥 3. Agent Social Board

In multi-agent scenarios, agents can post bet rationale, confidence levels, and position updates on a shared social board.