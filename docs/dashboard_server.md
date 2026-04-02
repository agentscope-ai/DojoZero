# Dashboard Server

The dashboard server is a central service for managing trials. While [`dojo0 run`](./single_trial.md) executes a single trial in your terminal, the dashboard lets you:

- **Run multiple trials concurrently** from a single server process
- **Auto-discover and schedule trials** for upcoming games via trial sources and sports API integration
- **Monitor trial status** through a unified API
- **Submit backtests** to the same server for centralized tracking (see [Backtesting](./backtesting.md))

If you're running a single trial to experiment, `dojo0 run` is all you need. Use the dashboard when you want to operate DojoZero as an always-on service.

## 1. Run via Dashboard Server

```bash
# Terminal 1
dojo0 serve

# Terminal 2
dojo0 run \
  --params trial_params/nba-moneyline.yaml \
  --trial-id nba-server-001 \
  --server http://localhost:8000
```

## 2. Scheduling with Trial Sources

```bash
dojo0 serve --trial-source "trial_sources/daily/*.yaml"
dojo0 list-sources
dojo0 list-trials
dojo0 remove-source <source_id>
dojo0 clear-schedules
```

## 3. Trial Source Parameters

`trial_sources/*.yaml` define what the dashboard server should discover (games) and how it should schedule trials for them.

At a minimum, most trial source files include:

| Field | Purpose |
|---|---|
| `source_id` | Stable ID for the discovery source (used by `list-sources` / `remove-source`). |
| `sport_type` | Which league adapter to use (for example `nba` or `nfl`). |
| `config` | Trial/template configuration applied to discovered games. |

Inside `config`, your template can be either:
- **Full template style**: include `scenario_name` and a full `scenario_config` (includes streams/operators/agent wiring).
- **Matrix/shortcut style**: provide `scenario_name` plus higher-level selections like `max_daily_games`, `personas`, and `llm_config_path` (the server uses base templates to expand these into a runnable trial for each discovered game).

Scheduling knobs (when present in your template) typically include:

| Field | Purpose |
|---|---|
| `pre_start_hours` | Start a trial this many hours before game start. |
| `check_interval_seconds` | How often to re-check game status / market readiness. |
| `auto_stop_on_completion` | Stop the trial when the game finishes. |
| `data_dir` | Output directory root for persistence artifacts. |
| `sync_interval_seconds` | How often to sync discovery data from the league API. |

## 4. Runtime and Storage Options

- `--store-directory`: trial state and checkpoint root.
- `--runtime-provider {local,ray}`: execution backend.
- `--ray-config`: Ray initialization YAML file.

## 5. Cluster Mode

Run multiple dashboard servers to distribute trial execution across machines. One server wins leader election and runs the scheduler; all servers accept trial submissions and execute trials.

### Single Server (default)

No extra flags needed — everything works as before:

```bash
dojo0 serve
```

### Dev Cluster (file-based election, static peers)

Start two or more servers on the same machine or network, pointing at a shared store:

```bash
# Server 1
dojo0 serve --port 8000 \
    --server-id server-1 \
    --server-url http://localhost:8000 \
    --cluster-peers http://localhost:8001 \
    --store-directory ./store

# Server 2
dojo0 serve --port 8001 \
    --server-id server-2 \
    --server-url http://localhost:8001 \
    --cluster-peers http://localhost:8000 \
    --store-directory ./store
```

Leader election determines which server runs the scheduler automatically — no need for `--no-scheduler`. Use `--no-scheduler` only if you want to disable scheduling on all servers (e.g. when submitting trials manually).

Leader election uses `fcntl.flock` on a file in the store directory. Both servers must share the same `--store-directory`.

### Production Cluster (Redis-based election and discovery)

```bash
dojo0 serve --port 8000 \
    --server-id server-1 \
    --server-url http://10.0.1.10:8000 \
    --cluster-redis-url redis://redis:6379
```

Or use an environment variable:

```bash
export DOJOZERO_CLUSTER_REDIS_URL=redis://redis:6379
dojo0 serve --port 8000 \
    --server-id server-1 \
    --server-url http://10.0.1.10:8000
```

Redis mode uses `SET NX EX` for leader election and a hash with TTL heartbeats for peer discovery. No manual Redis setup is required — keys are created automatically.

### CLI Flags

| Flag | Default | Description |
|---|---|---|
| `--server-id` | hostname | Unique identifier for this server instance |
| `--server-url` | `http://{host}:{port}` | Externally reachable URL for this server |
| `--cluster-peers` | (none) | Peer server URLs (repeatable, for static discovery) |
| `--cluster-redis-url` | `$DOJOZERO_CLUSTER_REDIS_URL` | Redis URL (enables Redis election + discovery) |

### How It Works

- **Trial distribution**: When a trial is submitted, the receiving server forwards it to the least-loaded peer. Each trial is tagged with `owner_server_id` in the store.
- **Gateway routing**: If a gateway request arrives at the wrong server, it is reverse-proxied to the owning server transparently.
- **Auto-resume**: On restart, each server only resumes trials it owns. Legacy trials (no owner) are resumed by any server.
- **Leader failover**: If the leader goes down, another server acquires leadership and starts the scheduler.

### Cluster Status API

```bash
# Check cluster state
curl http://localhost:8000/api/cluster/status

# Find which server owns a trial
curl http://localhost:8000/api/cluster/trial-location/{trial_id}
```

## What's Next

- **Observe trials**: Add `--trace-backend jaeger` to `dojo0 serve` to export OpenTelemetry traces for all trials managed by the server. See [Tracing](./tracing.md) for backend setup and [Arena](./arena.md) for a browser-based trace timeline.
- **Iterate offline**: Replay event logs from completed trials through different agent configurations with [Backtesting](./backtesting.md).
- **Deploy to production**: See [Deployment](./deployment.md) for Docker and cloud VM setup.
