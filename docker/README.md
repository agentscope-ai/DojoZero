# Docker (build from this repo)

Use this guide when you want to **build** the all-in-one image locally (`serve` + Arena + Jaeger in one container). Commands assume the **repository root** (parent of `docker/`).

| If you want… | See |
| :--- | :--- |
| Pull a prebuilt image and run with no build step | [Quick Start](../README.md) (`agentscope/dojozero`) |
| Multi-service deployment (NBA/NFL/NCAA containers, `deploy/up.sh`) | [Deployment](../docs/deployment.md) |
| Environment variables in depth | [Configuration](../docs/configuration.md) |

---

## Prerequisites

```bash
mkdir -p outputs data trial_params trial_sources
cp .env.example .env   # optional: template only; edit values in an editor, never commit `.env`
```

**Secrets:** Put API keys in a **file**, not on the command line — `export …` and `docker run -e KEY=…` end up in shell history and process listings. Compose loads an optional **repo-root `.env`** (see [.env.example](../.env.example); the real file is [gitignored](../.gitignore)). Edit it in your editor, or symlink it to a file elsewhere (`ln -sf ~/.config/dojozero/secrets.env .env`). For another path, use a **second compose file** (gitignored) that only sets `env_file` for the `dojozero` service.

**Scheduled trials (NBA/NFL):** `trial_sources/image/nba.yaml` and `nfl.yaml` use `agents/llms/claude.yaml`, so the container needs **`DOJOZERO_ANTHROPIC_API_KEY`**. Without it, trials fail with `API key environment variable 'DOJOZERO_ANTHROPIC_API_KEY' is not set`. Other names are in [.env.example](../.env.example) and [Configuration](../docs/configuration.md).

Optional: build the Arena static assets so a bind-mounted `frontend/dist` is not empty (otherwise the mount can hide the UI baked into the image):

```bash
cd frontend && npm install && npm run build && cd ..
```

See [Arena](../docs/arena.md) for dev vs production static serving.

---

## 1) Build the all-in-one image

```bash
docker build -f docker/allinone.Dockerfile -t dojozero:latest .
```

---

## 2) Run (choose one)

### A) Docker Compose (recommended for local use)

Matches `docker/docker-compose.allinone.local.yml`: volumes for `outputs`, `data`, read-only `trial_params` / `trial_sources`, optional `frontend/dist` → `arena-static`, schedule state volume, tracing env defaults, and `host.docker.internal` on Linux-friendly hosts.

After `.env` exists at the repo root (or a symlink), from the repo root:

```bash
docker compose -f docker/docker-compose.allinone.local.yml build
docker compose -f docker/docker-compose.allinone.local.yml up -d
```

`docker compose --env-file` only substitutes variables **inside the Compose file**; it does not replace service `env_file` for container secrets. Use repo-root `.env` or an override compose snippet as above.

Stop:

```bash
docker compose -f docker/docker-compose.allinone.local.yml down
```

Host ports default to **8000** (dashboard) and **3001** (Arena). To remap them, set `DOJOZERO_SERVE_PORT` / `DOJOZERO_ARENA_PORT` in the shell **when you invoke** `docker compose` (values are not secrets), or edit the `ports:` block in the compose file.

Optional service `env_file` uses `required: false` (Compose **v2.24+**). Upgrade the Compose plugin if parsing fails.

**Open:** Dashboard `http://localhost:8000`, Arena `http://localhost:3001`, Jaeger UI `http://localhost:16686`.

> **Tracing / Redis:** For Jaeger-only local tracing, avoid setting `DOJOZERO_REDIS_URL` unless you run the Redis sync path; otherwise Arena can stay on Redis mode with stale data (same note as in the compose file).

### B) `docker run` — minimal

No host volumes; good for a quick smoke test after a local build. Use **`--env-file`** so the key never appears on the command line (create a file with the same variables as [.env.example](../.env.example)).

```bash
docker run -d --name dojozero \
  --env-file ./.env \
  -p 8000:8000 \
  -p 3001:3001 \
  -p 16686:16686 \
  dojozero:latest
```

### C) `docker run` — same layout as Compose

Persistent `outputs` / `data`, read-only `trial_params` / `trial_sources`, schedule state volume (name aligned with Compose: `dojozero-aio-local-schedules`), tracing-related env, and optional Arena static override.

If `frontend/dist` is missing or empty, **omit** the `arena-static` volume line so the container uses the UI baked into the image.

```bash
docker run -d --name dojozero \
  --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  --env-file ./.env \
  -e PYTHONUNBUFFERED=1 \
  -e DOJOZERO_TRIAL_SOURCE='trial_sources/image/nba.yaml trial_sources/image/nfl.yaml' \
  -e DOJOZERO_SERVE_HOST=0.0.0.0 \
  -e DOJOZERO_SERVE_PORT=8000 \
  -e DOJOZERO_ARENA_HOST=0.0.0.0 \
  -e DOJOZERO_ARENA_PORT=3001 \
  -e DOJOZERO_TRACE_INGEST_ENDPOINT=http://127.0.0.1:4318 \
  -e DOJOZERO_ARENA_TRACE_BACKEND=jaeger \
  -e DOJOZERO_ARENA_TRACE_QUERY_ENDPOINT=http://127.0.0.1:16686 \
  -p 8000:8000 \
  -p 3001:3001 \
  -p 16686:16686 \
  -v "$(pwd)/outputs:/app/outputs" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/trial_params:/app/trial_params:ro" \
  -v "$(pwd)/trial_sources:/app/trial_sources:ro" \
  -v "$(pwd)/frontend/dist:/app/arena-static:ro" \
  -v dojozero-aio-local-schedules:/app/.dojozero \
  dojozero:latest
```

`--add-host=host.docker.internal:host-gateway` helps on Linux; on Docker Desktop (macOS/Windows) you can omit it if `host.docker.internal` already resolves inside the container.

---

## Useful commands

```bash
# Logs — Compose (service name)
docker compose -f docker/docker-compose.allinone.local.yml logs -f

# Logs — container named dojozero (docker run examples above)
docker logs -f dojozero

# Stop and remove a manually created container
docker stop dojozero && docker rm dojozero
```
