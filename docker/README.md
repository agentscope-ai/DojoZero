# Docker

Run DojoZero locally with Docker build-based workflows.
All commands below assume you are at repository root (parent of `docker/`).

> If you want the pull-and-run all-in-one image flow, use the Quick Start section in `README.md`.

## Prerequisites

```bash
# 1) Prepare env file
cp .env.example .env
# Fill at least:
# - DOJOZERO_DASHSCOPE_API_KEY
# - DOJOZERO_TAVILY_API_KEY

# 2) Prepare host folders
mkdir -p outputs data trial_params trial_sources frontend-update/dist
```

**Arena + Jaeger (local):** leave `DOJOZERO_REDIS_URL` unset in `.env`. Redis is for the optional SLS→Redis sync path; pointing Arena at Redis without the sync service can leave landing page scores stale.

## Option 1: Build and Run All-in-One

Use `docker/allinone.Dockerfile` to build one image that runs all-in-one services
(serve + arena + jaeger in one container).

### 1) Build the all-in-one image

```bash
docker build -f docker/allinone.Dockerfile -t dojozero:latest .
```

### 2) Run the all-in-one image directly (`docker run`)

```bash
docker run -d --name dojozero \
  --env-file .env \
  -e DOJOZERO_TRIAL_SOURCE='trial_sources/*.yaml' \
  -p 8000:8000 \
  -p 3001:3001 \
  -p 16686:16686 \
  -v "$(pwd)/outputs:/app/outputs" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/trial_params:/app/trial_params:ro" \
  -v "$(pwd)/trial_sources:/app/trial_sources:ro" \
  -v "$(pwd)/frontend-update/dist:/app/arena-static:ro" \
  dojozero:latest
```

Open:
- Dashboard: `http://localhost:8000`
- Arena: `http://localhost:3001`
- Jaeger: `http://localhost:16686`

### 3) Run all-in-one with Compose (build + up)

`docker/docker-compose.allinone.local.yml` provides the same all-in-one flow in Compose form.

```bash
# Build image defined by compose file
docker compose -f docker/docker-compose.allinone.local.yml build

# Start in background
docker compose -f docker/docker-compose.allinone.local.yml up -d
```

Stop:

```bash
docker compose -f docker/docker-compose.allinone.local.yml down
```

## Build Frontend for Arena (Optional)

From repo root:

```bash
cd frontend-update
npm ci
VITE_API_URL=http://localhost:3001 npm run build
```

Static files in `frontend-update/dist` are mounted to `/app/arena-static`.

## Useful Commands

```bash
# Logs
docker logs -f dojozero
docker compose -f docker/docker-compose.allinone.local.yml logs -f

# Remove local all-in-one container
docker stop dojozero && docker rm dojozero
```
