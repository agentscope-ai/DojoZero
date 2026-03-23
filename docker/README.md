# Docker

Run DojoZero in Docker on your machine. All commands below assume you are at the **repository root** (parent of `docker/`).

**What you can run here**

| Goal | What starts | Section |
|------|-------------|---------|
| Run a trial once, then exit | `dojo0 run` | [Single trial](#1-single-trial-dojo0-run) |
| Long-running dashboard / API | `dojo0 serve` | [Dashboard](#2-dashboard-server-dojo0-serve) |
| Same + trace UI (Jaeger) | `serve` + Jaeger | [Dashboard + Jaeger](#3-dashboard--jaeger-traces) |
| Arena UI (needs traces) | `serve` + Jaeger + Arena | [Arena](#4-arena--frontend-optional) |

Compose uses **profiles**: only the services you ask for are created. A plain `docker compose … config` does not start everything at once.

---

## Prerequisites

1. Copy env file:

   ```bash
   cp .env.example .env
   ```

2. **Required** for `trial` and `serve` (put in `.env`):

   - `DOJOZERO_DASHSCOPE_API_KEY`
   - `DOJOZERO_TAVILY_API_KEY`

3. Optional variables are documented in [`.env.example`](../.env.example). Handy ones:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DOJOZERO_SERVE_PORT` | `8000` | Browser port for the dashboard |
| `DOJOZERO_TRIAL_SOURCE` | `trial_sources/*.yaml` | What `dojo0 serve --trial-source` loads (glob OK) |
| `DOJOZERO_ENABLE_JAEGER` | `0` | Set to `1` when using the `tracing` profile so the dashboard can export traces |
| `DOJOZERO_ARENA_PORT` | `3001` | Browser port for Arena when using `--profile arena` |

---

## Profiles (switches)

| Profile | Service | In plain terms |
|---------|---------|----------------|
| `trial` | `trial` | One container runs **`dojo0 run`** once and stops |
| `serve` | `serve` | **`dojo0 serve`** keeps running: HTTP API, scheduling, gateway |
| `tracing` | `jaeger` | **Jaeger v2** (`jaegertracing/jaeger:2.16.0`): trace collector + UI on port **16686** (OTLP **4317** / **4318**). 
| `arena` | `arena` | **`dojo0 arena`**: reads traces from Jaeger at `http://jaeger:16686`, serves API/WebSocket and (if built) the SPA from **`frontend-update/dist`** |

---

## 1) Single trial (`dojo0 run`)

```bash
docker compose -f docker/docker-compose.local.yml --profile trial run --rm trial
```

Defaults (override in `.env` or on the command line):

- `DOJOZERO_TRIAL_PARAMS` → `trial_params/nba-moneyline.yaml`
- `DOJOZERO_TRIAL_ID` → `quickstart-trial`

NFL example:

```bash
DOJOZERO_TRIAL_PARAMS=trial_params/nfl-moneyline.yaml \
DOJOZERO_TRIAL_ID=quickstart-nfl \
docker compose -f docker/docker-compose.local.yml --profile trial run --rm trial
```

`trial_params/` on your machine is mounted **read-only** into the container—edit YAML on the host, no image rebuild.

---

## 2) Dashboard server (`dojo0 serve`)

```bash
docker compose -f docker/docker-compose.local.yml --profile serve up -d --build
```

- Open: `http://localhost:${DOJOZERO_SERVE_PORT:-8000}`
- Scheduler data lives in Docker volume **`dojozero-local-schedules`** → `/app/.dojozero`

**Only NBA or only NFL** (single source file):

```bash
DOJOZERO_TRIAL_SOURCE=trial_sources/nba.yaml \
docker compose -f docker/docker-compose.local.yml --profile serve up -d --build
```

**Stop** (match the profiles you used):

```bash
docker compose -f docker/docker-compose.local.yml --profile serve down
```

If Jaeger was also running, include `tracing`:

```bash
docker compose -f docker/docker-compose.local.yml --profile serve --profile tracing down
```

To **wipe scheduler state**, add `--volumes` (removes `dojozero-local-schedules`).

---

## 3) Dashboard + Jaeger traces

1. In `.env`:

   ```bash
   DOJOZERO_ENABLE_JAEGER=1
   ```

2. Start:

   ```bash
   docker compose -f docker/docker-compose.local.yml --profile serve --profile tracing up -d --build
   ```

3. Jaeger UI: [http://localhost:16686](http://localhost:16686)

The `serve` container sends traces to Jaeger over the Compose network (`http://jaeger:4318`), same idea as [Jaeger getting started](https://www.jaegertracing.io/docs/latest/getting-started/).

**Important:** If you run `serve` **without** the `tracing` profile, keep `DOJOZERO_ENABLE_JAEGER=0`; otherwise export will fail.

---

## 4) Arena + frontend (optional)

Arena needs Jaeger’s query API on the Docker network (`http://jaeger:16686`). The React app is **built on the host** and mounted into the container (not inside the Python image).

### Build the frontend (after UI changes)

From repo root, `VITE_API_URL` must match how you open Arena in the browser (default compose uses **3001**):

```bash
cd frontend-update
npm ci
VITE_API_URL=http://localhost:3001 npm run build
```

### Start stack (typical: serve + Jaeger + Arena)

```bash
# .env: DOJOZERO_ENABLE_JAEGER=1
docker compose -f docker/docker-compose.local.yml --profile serve --profile tracing --profile arena up -d --build
```

- Arena: `http://localhost:${DOJOZERO_ARENA_PORT:-3001}`
- If `frontend-update/dist/index.html` is missing, Arena still runs (API/WebSocket only); check container logs for the build hint.

### Stop

Pass the same profiles you used with `up`:

```bash
docker compose -f docker/docker-compose.local.yml --profile serve --profile tracing --profile arena down
```

---

## Host folders ↔ container

| On your machine | In container | Notes |
|-----------------|--------------|--------|
| `./outputs` | `/app/outputs` | Events / persistence from trial params |
| `./data` | `/app/data` | Data / cache |
| `./trial_params` | `/app/trial_params` | Read-only |
| `./trial_sources` | `/app/trial_sources` | Read-only |
| `./frontend-update/dist` | `/app/arena-static` | Read-only; `arena` profile; build with Vite first |
| Volume `dojozero-local-schedules` | `/app/.dojozero` | `serve` only |

---

## Rebuild images

`trial`, `serve`, and `arena` use the **same** image; enable the profiles you care about when building.

After `git pull` or dependency changes:

```bash
docker compose -f docker/docker-compose.local.yml --profile trial --profile serve --profile arena build --no-cache
```

Single service:

```bash
docker compose -f docker/docker-compose.local.yml --profile trial build --no-cache trial
docker compose -f docker/docker-compose.local.yml --profile serve build --no-cache serve
docker compose -f docker/docker-compose.local.yml --profile arena build --no-cache arena
```

Jaeger is a pulled public image—no local build.

If **`uv export --frozen`** fails after updating deps, run **`uv lock`** at the repo root and commit **`uv.lock`**.
