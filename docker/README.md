# Local Docker

This folder is for **local** workflows in Docker: **one-shot trials** (`dojo0 run`), **dashboard** (`dojo0 serve`), optional **Jaeger**, and optional **Arena** (`dojo0 arena` + mounted Vite build), **without** using [`deploy/`](../deploy/) (Alibaba / production-style).

| File | Purpose |
|------|---------|
| [`local.Dockerfile`](./local.Dockerfile) | Python 3.11 + `uv`: first stage installs deps from **`uv.lock`** (`uv export` → `uv pip install -r`), then copies `src/` / `agents/` / … and `uv pip install .` (better layer cache). |
| [`docker-compose.local.yml`](./docker-compose.local.yml) | Compose **profiles**: `trial`, `serve`, `tracing` (Jaeger **v2**), **`arena`** (Arena UI/API; mounts `frontend-update/dist`). |

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Compose v2 (`docker compose …`)
- Compose file uses `depends_on` with `required: false` (needs a reasonably recent Compose; Docker Desktop is fine)

Run commands from the **repository root** in the examples below (easiest for `cp .env.example .env`).  
Bind mounts and `env_file` paths in the compose file are **relative to the compose file’s directory** (`docker/`), so `../.env` and `../outputs` still point at the repo root even if your shell `cwd` differs.

## Environment (`.env`)

```bash
cp .env.example .env
```

Minimum for both `trial` and `serve`:

- `DOJOZERO_DASHSCOPE_API_KEY`
- `DOJOZERO_TAVILY_API_KEY`

Optional for `serve` (see comments in [`.env.example`](../.env.example)):

| Variable | Default | Meaning |
|----------|---------|---------|
| `DOJOZERO_SERVE_PORT` | `8000` | Host port mapped to dashboard `8000` |
| `DOJOZERO_TRIAL_SOURCE` | `trial_sources/*.yaml` | Passed to `dojo0 serve --trial-source` (glob supported) |
| `DOJOZERO_ENABLE_JAEGER` | `0` | Set `1` when using profile `tracing` to export traces to Jaeger |
| `DOJOZERO_ARENA_PORT` | `3001` | Host port for Arena when using `--profile arena` |

## Profiles

| Profile | Service | What it does |
|---------|---------|----------------|
| `trial` | `trial` | Runs **`dojo0 run` once** and exits |
| `serve` | `serve` | Runs **`dojo0 serve`** (HTTP API, scheduling, gateway) |
| `tracing` | `jaeger` | **Jaeger v2** image **`jaegertracing/jaeger:2.16.0`** — OTLP `4317`/`4318`, UI `16686` (v1 `all-in-one` is archived) |
| `arena` | `arena` | **`dojo0 arena`** — reads traces from Jaeger Query **`http://jaeger:16686`**, serves API/WebSocket + optional SPA from **`frontend-update/dist`** |

Services are **opt-in** via profiles so a plain `docker compose … config` does not assume you want every stack piece.

---

## 1) Single trial (`dojo0 run`)

```bash
docker compose -f docker/docker-compose.local.yml --profile trial run --rm trial
```

Defaults (overridable via environment on the same command line or in `.env`):

- `DOJOZERO_TRIAL_PARAMS` → `trial_params/nba-moneyline.yaml`
- `DOJOZERO_TRIAL_ID` → `quickstart-trial`

NFL example:

```bash
DOJOZERO_TRIAL_PARAMS=trial_params/nfl-moneyline.yaml \
DOJOZERO_TRIAL_ID=quickstart-nfl \
docker compose -f docker/docker-compose.local.yml --profile trial run --rm trial
```

Edit `trial_params/*.yaml` on the host; that directory is **bind-mounted** read-only.

---

## 2) Dashboard server (`dojo0 serve`)

Starts the same dashboard flow as in the main README, but inside Docker:

```bash
docker compose -f docker/docker-compose.local.yml --profile serve up -d --build
```

- Dashboard: `http://localhost:${DOJOZERO_SERVE_PORT:-8000}`
- Scheduler / store state: **named volume** `dojozero-local-schedules` → `/app/.dojozero`

**Only NBA or only NFL** (single source file):

```bash
DOJOZERO_TRIAL_SOURCE=trial_sources/nba.yaml \
docker compose -f docker/docker-compose.local.yml --profile serve up -d --build
```

Stop:

```bash
docker compose -f docker/docker-compose.local.yml --profile serve down
```

If you also started **Jaeger**, use the same profiles so that container is torn down:

```bash
docker compose -f docker/docker-compose.local.yml --profile serve --profile tracing down
```

To remove the scheduler volume and reset local schedules: add **`--volumes`** (deletes named volume `dojozero-local-schedules`).

---

## 3) Dashboard + Jaeger traces

1. In `.env` set:

   ```bash
   DOJOZERO_ENABLE_JAEGER=1
   ```

2. Start both profiles:

   ```bash
   docker compose -f docker/docker-compose.local.yml --profile serve --profile tracing up -d --build
   ```

3. Open **Jaeger UI**: [http://localhost:16686](http://localhost:16686)

`serve` sends OTLP HTTP to `http://jaeger:4318` on the Compose network (same as [Jaeger getting started](https://www.jaegertracing.io/docs/latest/getting-started/)). The compose file pins **`jaegertracing/jaeger`** (v2); the old **`jaegertracing/all-in-one`** image was the v1 line and is no longer the supported default.

If you start **`serve` without** profile `tracing`, leave `DOJOZERO_ENABLE_JAEGER=0` (or traces will fail to export).

---

## 4) Arena + frontend (optional)

Arena queries **Jaeger** for traces and exposes REST + WebSockets; the React app in [`frontend-update/`](../frontend-update/) is **built on the host** and **bind-mounted** into the container (not baked into the Python image).

### 4.1 Build the frontend (once per change)

From the repo root, use a **`VITE_API_URL`** that matches how you open Arena in the browser (default compose maps **`http://localhost:3001`**):

```bash
cd frontend-update
npm ci
VITE_API_URL=http://localhost:3001 npm run build
```

If you set **`DOJOZERO_ARENA_PORT`** to something other than `3001`, rebuild with that port in `VITE_API_URL` (the value is compiled into the JS bundle).

### 4.2 Start Jaeger + Arena (and usually `serve`)

Arena needs the **Jaeger Query API** on the Docker network (`http://jaeger:16686`). Typical stack:

```bash
# .env: DOJOZERO_ENABLE_JAEGER=1
docker compose -f docker/docker-compose.local.yml --profile serve --profile tracing --profile arena up -d --build
```

- **Arena UI + API:** `http://localhost:${DOJOZERO_ARENA_PORT:-3001}`  
- If **`frontend-update/dist/index.html`** is missing, Arena still starts (API/WebSocket only); the container logs print a build hint.

### 4.3 Stop

Include **`arena`** (and **`tracing`**) on `down` if you started them:

```bash
docker compose -f docker/docker-compose.local.yml --profile serve --profile tracing --profile arena down
```

---

## 5) Host mounts & outputs

| Host (repo) | Container | Role |
|-------------|-----------|------|
| `./outputs` | `/app/outputs` | Event JSONL / persistence paths from params |
| `./data` | `/app/data` | Data / cache |
| `./trial_params` | `/app/trial_params` | Params (read-only) |
| `./trial_sources` | `/app/trial_sources` | Trial source YAML (read-only) |
| `./frontend-update/dist` | `/app/arena-static` | Arena SPA (**read-only**, **`arena` profile**); build with Vite first |
| *(volume)* `dojozero-local-schedules` | `/app/.dojozero` | Scheduler store ( **`serve` only** ) |

---

## 6) Rebuild images

`trial`, `serve`, and `arena` share the same image (**profiles**); enable the profiles you need when building.

After `git pull` or dependency changes:

```bash
docker compose -f docker/docker-compose.local.yml --profile trial --profile serve --profile arena build --no-cache
```

Rebuild one service (still pass profiles so the service is part of the project):

```bash
docker compose -f docker/docker-compose.local.yml --profile trial build --no-cache trial
docker compose -f docker/docker-compose.local.yml --profile serve build --no-cache serve
docker compose -f docker/docker-compose.local.yml --profile arena build --no-cache arena
```

`jaeger` uses a public image (`pull` on first `up`); no local build.

If **`uv export --frozen`** fails after pulling, run **`uv lock`** at the repo root and commit an updated **`uv.lock`**.

---

## 7) What is in the image

(Single `FROM` image with **ordered layers**, not a multi-stage build.)

- **Layer A:** `pyproject.toml`, `uv.lock`, `README.md` → `uv export --frozen --no-dev --no-emit-project` → `uv pip install --system -r …` (third-party only).
- **Layer B:** `src/`, `agents/`, `trial_sources/`, `trial_params/` → `uv pip install --system .` (installs `dojozero` + `dojo0`).

No Alibaba extras by default. For **OSS / SLS / Redis** in the image, extend the Dockerfile (e.g. second `uv pip install` with `'.[alicloud,redis]'` or adjust `uv export` with `--extra`) — see [`docs/installation.md`](../docs/installation.md).

**Build context:** [`.dockerignore`](../.dockerignore) excludes e.g. `deploy/`, `docker/`, `tests/`, `outputs/`, `data/` (smaller upload). The Dockerfile path is still `docker build -f docker/local.Dockerfile .` from the repo root; `-f` is read from disk, not from the context archive.

Code under `agents/` is **not** bind-mounted; change → rebuild image.

---

## 8) vs `deploy/`

| | `docker/` | `deploy/` |
|---|-----------|-----------|
| Use case | Local trial + dashboard + optional Jaeger + optional Arena (mounted UI) | Alibaba (SLS, OSS, mirrors, …) |
| Docs | This file | [`deploy/DEPLOYMENT.md`](../deploy/DEPLOYMENT.md) |

---

## 9) Troubleshooting

- **`no such service: trial`** — Add `--profile trial` (e.g. `run --rm trial`).
- **Empty `docker compose … config` / no services** — All services use **profiles**; enable at least one profile, or nothing is selected (expected).
- **Missing API key errors** — Fill `.env` at repo root; compose uses `env_file: ../.env` (path relative to `docker-compose.local.yml`).
- **Jaeger enabled but no traces** — Use `--profile tracing`, ensure `DOJOZERO_ENABLE_JAEGER=1`, restart `serve`.
- **Jaeger container left running after `down`** — You started with `--profile tracing` but ran `down` with only `--profile serve`; include **`--profile tracing`** on `down`, or `docker stop` the Jaeger container.
- **Port 8000 in use** — Set `DOJOZERO_SERVE_PORT` in `.env` and `up` again.
- **Build fails at `uv export --frozen`** — Run **`uv lock`** at the repo root and ensure **`uv.lock`** is committed / present in the build context (not listed in `.dockerignore`).
- **Arena empty / errors talking to Jaeger** — Start with **`--profile tracing`** so `jaeger` is on the network; Arena uses **`http://jaeger:16686`** (Query API), not OTLP `4318`.
- **Arena UI loads but API calls fail** — Rebuild the frontend with **`VITE_API_URL=http://localhost:<DOJOZERO_ARENA_PORT>`** matching the host port you mapped.
- **Arena `healthcheck` slow / unhealthy** — First startup waits for cache warm-up; **`start_period: 90s`** is set. If Jaeger has no data yet, some steps may still be slow.
