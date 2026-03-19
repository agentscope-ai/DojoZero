# Running and Scheduling Trials

This guide covers local trial execution, server-based orchestration, and automatic scheduling.

## 1) Run a Single Trial Locally

Use a trial params file from `trial_params/`:

```bash
dojo0 run --params trial_params/nba-moneyline.yaml --trial-id nba-local-001
```

If `--trial-id` is omitted, DojoZero generates a UUID.

## 2) Resume an Interrupted Trial

```bash
# Resume from a specific checkpoint
dojo0 run --trial-id nba-local-001 --checkpoint-id <checkpoint_id>

# Resume latest checkpoint
dojo0 run --trial-id nba-local-001 --resume-latest
```

You can also start from params + checkpoint together.

## 3) Run Through Dashboard Server

Start tracing backend and dashboard:

```bash
dojo0 serve --trace-backend jaeger
```

Submit trial from another terminal:

```bash
dojo0 run \
  --params trial_params/nba-moneyline.yaml \
  --trial-id nba-server-001 \
  --server http://localhost:8000
```

## 4) Automatic Scheduling with Trial Sources

Trial sources let the server discover upcoming games and schedule trials before start time.

Run server with one or more sources:

```bash
dojo0 serve --trace-backend jaeger --trial-source "trial_sources/*.yaml"
```

Manage sources and schedules:

```bash
dojo0 list-sources
dojo0 list-trials
dojo0 remove-source <source_id>
dojo0 clear-schedules
```

## 5) Runtime and Store Options

These CLI options apply to `run`, `backtest`, and/or `serve`:

- `--store-directory`: trial state and checkpoint storage path
- `--runtime-provider {local,ray}`: select execution backend
- `--ray-config`: Ray initialization YAML

Example:

```bash
dojo0 run \
  --params trial_params/nfl-moneyline.yaml \
  --store-directory ./dojozero-store \
  --runtime-provider local
```

## 6) Auto-Resume Behavior in Server Mode

By default, `dojo0 serve` attempts to recover trials that were active before shutdown:

1. Finds trials in STARTING/RUNNING states
2. Locates latest checkpoint
3. Restarts from checkpoint (when not stale)

Useful options:

- `--no-auto-resume`: disable this behavior
- `--stale-threshold-hours`: skip very old checkpoints
