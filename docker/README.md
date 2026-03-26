# Docker (build from this repo)

Use this guide when you want to **build** the all-in-one image locally (`serve` + Arena + Jaeger in one container). Commands assume the **repository root** (parent of `docker/`).

## Before you start

```bash
cp .env.example .env   # edit values; never commit `.env`
```

## Build

```bash
docker build -f docker/allinone.Dockerfile -t dojozero:latest .
```

## Run

```bash
docker run -d --name dojozero \
  --env-file ./.env \
  -p 8000:8000 -p 3001:3001 -p 16686:16686 \
  dojozero:latest
```

### `docker run` — persistent data on the host

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

### Docker Compose

[`docker/docker-compose.allinone.local.yml`](docker-compose.allinone.local.yml) mirrors the persistent `docker run` layout (volumes, tracing env, optional `frontend/dist` mount). From repo root, with `.env` at the root:

First time or after Dockerfile changes:

```bash
docker compose -f docker/docker-compose.allinone.local.yml up -d --build
```

Later (image already built):

```bash
docker compose -f docker/docker-compose.allinone.local.yml up -d
```

Stop:

```bash
docker compose -f docker/docker-compose.allinone.local.yml down
```

## Logs and cleanup

```bash
docker logs -f dojozero
docker stop dojozero && docker rm dojozero
```

With Compose:

```bash
docker compose -f docker/docker-compose.allinone.local.yml logs -f
```
