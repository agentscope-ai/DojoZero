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
dojo0 serve --trial-source "trial_sources/image/*.yaml"
dojo0 list-sources
dojo0 list-trials
dojo0 remove-source <source_id>
dojo0 clear-schedules
```

## 3. Trial Source Parameters

`trial_sources/image/*.yaml` define what the dashboard server should discover (games) and how it should schedule trials for them.

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

Run multiple dashboard servers to distribute trial execution across machines. One server wins leader election and runs the scheduler; all servers accept trial submissions and execute trials. Cluster mode requires Redis.

### Single Server (default)

No extra flags needed — everything works as before:

```bash
dojo0 serve
```

### Cluster (Redis-based election, discovery, and shared state)

```bash
# Server 1
dojo0 serve --port 8000 \
    --server-id server-1 \
    --server-url http://localhost:8000 \
    --cluster-redis-url redis://localhost:6379/0 \
    --trial-source trial_sources/image/nba.yaml

# Server 2
dojo0 serve --port 8001 \
    --server-id server-2 \
    --server-url http://localhost:8001 \
    --cluster-redis-url redis://localhost:6379/0 \
    --trial-source trial_sources/image/nba.yaml
```

Or use an environment variable:

```bash
export DOJOZERO_CLUSTER_REDIS_URL=redis://redis:6379/0
dojo0 serve --port 8000 \
    --server-id server-1 \
    --server-url http://10.0.1.10:8000
```

Leader election determines which server runs the scheduler automatically — no need for `--no-scheduler`. Use `--no-scheduler` only if you want to disable scheduling on all servers (e.g. when submitting trials manually).

### CLI Flags

| Flag | Default | Description |
|---|---|---|
| `--server-id` | hostname | Unique identifier for this server instance |
| `--server-url` | Auto-detected (see below) | Externally reachable URL for this server |
| `--cluster-redis-url` | `$DOJOZERO_CLUSTER_REDIS_URL` | Redis URL (enables cluster mode) |

### Kubernetes / Container Setup

When pods listen on `0.0.0.0` (the default), each server must advertise a **routable IP** so peers can forward trials to it. If `--server-url` is not set and the bind host is `0.0.0.0`, the server auto-detects its IP using the `POD_IP` environment variable (with a `socket.gethostbyname()` fallback).

Expose `POD_IP` via the Kubernetes Downward API in your pod spec:

```yaml
env:
  - name: POD_IP
    valueFrom:
      fieldRef:
        fieldPath: status.podIP
```

This ensures each peer registers as e.g. `http://10.0.1.42:8000` instead of `http://0.0.0.0:8000`. Without this, trial forwarding between peers will loop back to the sender.

For local development with multiple servers on different ports, this is not needed — `http://localhost:8000` and `http://localhost:8001` are already distinct and reachable.

### How It Works

- **Leader election**: Uses `SET NX EX` with a Lua script for atomic renewal. TTL is 30 seconds, renewed every 5 seconds.
- **Peer discovery**: Each server heartbeats into a Redis hash (`dojozero:peers`) with a soft TTL. Stale peers are filtered out automatically.
- **Shared schedules**: In cluster mode, schedules and trial sources are stored in Redis (`dojozero:schedules`, `dojozero:trial_sources`) instead of local files, so any server that becomes leader sees the same state.
- **Trial distribution**: When a trial is submitted, the receiving server forwards it to the least-loaded peer. Each trial is tagged with `owner_server_id` in the store.
- **Gateway routing**: If a gateway request arrives at the wrong server, it is reverse-proxied to the owning server transparently.
- **Read-only endpoints**: Non-leader servers serve `/api/scheduled-trials` and `/api/trial-sources` by reading directly from Redis, so requests behind a load balancer get valid responses from any server.
- **Auto-resume**: On restart, each server only resumes trials it owns. Legacy trials (no owner) are resumed by any server.
- **Leader failover**: If the leader goes down, another server acquires leadership and starts the scheduler.
- **Source authority**: The leader always overwrites `dojozero:trial_sources` with its local YAML on startup. Stale sources from a previous deployment are removed automatically.

### Redis Keys

| Key | Type | TTL | Purpose |
|---|---|---|---|
| `dojozero:leader` | string | 30s | Leader lock (server ID of current leader) |
| `dojozero:peers` | hash | Soft (heartbeat-based) | Peer registry (`server_id → {url, active_trials, last_heartbeat}`) |
| `dojozero:schedules` | hash | None | Scheduled trials (`schedule_id → JSON`) |
| `dojozero:trial_sources` | hash | None | Trial source configs (`source_id → JSON`) |
| `dojozero:game_claims` | hash | None | Game dedup (`sport:game_id → server_id`) |
| `dojozero:trial_owners` | hash | None | Trial routing (`trial_id → {server_id, server_url}`) |

### Redis Cleanup on Redeployment

Flush all `dojozero:*` keys before starting a new deployment:

```bash
redis-cli -u $CLUSTER_REDIS_URL --scan --pattern 'dojozero:*' | xargs redis-cli -u $CLUSTER_REDIS_URL DEL
```

In a Docker/K8s deployment, add this as an init container or pre-deploy job.

### Trial IDs

| Context | ID Format | Example | Dedup |
|---|---|---|---|
| Scheduled trial | `{sport}-game-{game_id}-{hash}` | `nba-game-401810490-a3f1b2c4` | Schedule ID (`{sport}-game-{game_id}`) is deterministic; trial ID is unique per run |
| Adhoc NBA | `adhoc_nba_game_{game_id}_{hash}` | `adhoc_nba_game_401810490_c7d8e9f0` | No dedup (each run is unique) |
| Adhoc NCAA | `adhoc_ncaa_game_{game_id}_{hash}` | `adhoc_ncaa_game_401810490_b5a6c7d8` | No dedup (each run is unique) |

The hash suffix ensures each run is unique in SLS traces and the orchestrator store, even if the same game is re-run.

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
