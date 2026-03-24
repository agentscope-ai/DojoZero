# DojoZero - Deployment Guide

## Quick Start (Docker)

```bash
# 1. Configure environment
cp deploy/.env.template .env
nano .env  # Fill in API keys, credentials, and DOJOZERO_ENV

# 2. Build and run
deploy/up.sh --build

# China (use mirrors for faster builds):
CHINA_MIRRORS=true deploy/up.sh --build

# 3. Verify
docker logs dojozero-nba --tail 50
docker logs dojozero-nfl --tail 50
docker logs dojozero-ncaa --tail 50
curl http://localhost:8001/health  # NBA
curl http://localhost:8002/health  # NFL
curl http://localhost:8003/health  # NCAA
```

Three containers run separately for NBA (port 8001), NFL (port 8002), and NCAA (port 8003). Each automatically discovers games from ESPN and schedules trials. No cron needed.

---

## Environment Tiers

Trial sources are organized into tiers under `trial_sources/{daily,pre,prod}/`:

| Tier | `DOJOZERO_ENV` | Personas | Models | Max Games |
|------|-----------------|----------|--------|-----------|
| **daily** | `daily` | degen only | claude | 1 |
| **pre** | `pre` | all 6 | all models | 1 |
| **prod** | `prod` | all 6 | all models | unlimited |

Set `DOJOZERO_ENV` in `.env` to select the tier:

```bash
# In .env
DOJOZERO_ENV=prod
```

Override per-invocation:

```bash
DOJOZERO_ENV=daily deploy/up.sh
```

**Important:** Use `deploy/up.sh` instead of `docker-compose` directly. The script sources `.env` from the project root so that `DOJOZERO_ENV` is available for both compose-time variable substitution (trial source paths) and container runtime.

---

## Architecture

```
┌──────────────────────────┐ ┌──────────────────────────┐ ┌──────────────────────────┐
│ dojozero-nba (port 8001) │ │ dojozero-nfl (port 8002) │ │ dojozero-ncaa (port 8003)│
│                          │ │                          │ │                          │
│ dojo0 serve              │ │ dojo0 serve              │ │ dojo0 serve              │
│   --trial-source         │ │   --trial-source         │ │   --trial-source         │
│   {tier}/nba.yaml        │ │   {tier}/nfl.yaml        │ │   {tier}/ncaa.yaml       │
│                          │ │                          │ │                          │
│ ┌──────────────────────┐ │ │ ┌──────────────────────┐ │ │ ┌──────────────────────┐ │
│ │ ScheduleManager      │ │ │ │ ScheduleManager      │ │ │ │ ScheduleManager      │ │
│ │ - ESPN sync (hourly) │ │ │ │ - ESPN sync (hourly) │ │ │ │ - ESPN sync (hourly) │ │
│ │ - Game discovery     │ │ │ │ - Game discovery     │ │ │ │ - Game discovery     │ │
│ │ - Trial lifecycle    │ │ │ │ - Trial lifecycle    │ │ │ │ - Trial lifecycle    │ │
│ └──────────────────────┘ │ │ └──────────────────────┘ │ │ └──────────────────────┘ │
│           │              │ │           │              │ │           │              │
│           ▼              │ │           ▼              │ │           ▼              │
│ ┌───────┬──────┬───────┐ │ │ ┌───────┬──────┬───────┐ │ │ ┌───────┬──────┬───────┐ │
│ │  SLS  │ OSS  │ JSONL │ │ │ │  SLS  │ OSS  │ JSONL │ │ │ │  SLS  │ OSS  │ JSONL │ │
│ └───────┴──────┴───────┘ │ │ └───────┴──────┴───────┘ │ │ └───────┴──────┴───────┘ │
└──────────────────────────┘ └──────────────────────────┘ └──────────────────────────┘
               │                         │                         │
               └─────────────────────────┼─────────────────────────┘
                                         ▼
                                 Shared volumes:
                                 - ./outputs/
                                 - ./data/
                                 - ./trial_sources/ (read-only)
                                 - .env
```

---

## Cloud VM Deployment (ECS/EC2)

### Setup Script

```bash
# SSH into your server
ssh user@your-server-ip

# Clone the repo
git clone https://github.com/your-org/DojoZero.git
cd DojoZero

# Run setup script
chmod +x deploy/setup.sh

# International:
./deploy/setup.sh --docker

# China (configures Docker daemon and build mirrors):
./deploy/setup.sh --docker --china

# Log out and back in for docker group (if Docker was just installed)
exit
# SSH back in

# Configure environment
cd DojoZero
nano .env  # Fill in credentials, set DOJOZERO_ENV=prod

# Build and run
deploy/up.sh --build

# China:
CHINA_MIRRORS=true deploy/up.sh --build
```

The setup script:
- Installs Docker and docker-compose if missing
- With `--china`: Configures Docker daemon with DaoCloud mirror
- Creates .env from template

### Verify Deployment

```bash
# Check container status
docker ps

# View logs
docker logs dojozero-nba --tail 100
docker logs dojozero-nfl --tail 100
docker logs dojozero-ncaa --tail 100

# Health checks
curl http://localhost:8001/health  # NBA
curl http://localhost:8002/health  # NFL
curl http://localhost:8003/health  # NCAA

# Check scheduled trials
curl http://localhost:8001/api/scheduled-trials  # NBA
curl http://localhost:8002/api/scheduled-trials  # NFL
curl http://localhost:8003/api/scheduled-trials  # NCAA
```

---

## Configuration

### Environment Variables

Copy `deploy/.env.template` to `.env` and fill in:

| Variable | Required | Description |
|----------|----------|-------------|
| `DOJOZERO_ENV` | No | Tier: `daily`, `pre`, or `prod` (default: `daily`) |
| `DOJOZERO_DASHSCOPE_API_KEY` | Yes | LLM API key for agent reasoning |
| `DOJOZERO_TAVILY_API_KEY` | Yes | Web search API key |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` | Yes | Alibaba Cloud credentials |
| `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | Yes | Alibaba Cloud credentials |
| `DOJOZERO_SLS_ENDPOINT` | Yes | SLS endpoint (e.g., `cn-wulanchabu.log.aliyuncs.com`) |
| `DOJOZERO_SLS_PROJECT` | Yes | SLS project name |
| `DOJOZERO_SLS_LOGSTORE` | Yes | SLS logstore (default: `dojozero-traces`) |
| `DOJOZERO_OSS_ENDPOINT` | Yes | OSS endpoint |
| `DOJOZERO_OSS_BUCKET` | Yes | OSS bucket name |
| `DOJOZERO_OSS_PREFIX` | No | Key prefix (e.g., `prod/`) |
| `TZ` | No | Timezone (default: `UTC`) |

### Trial Source Configuration

Trial sources use a compact YAML format in `trial_sources/{tier}/{sport}.yaml`:

```yaml
source_id: nba-moneyline-source
sport_type: nba
config:
  scenario_name: nba
  max_concurrent_games: 0    # 0 = unlimited
  personas: [degen, mystic, pundit, shark, sheep, whale]
  llm_config_path: agents/llms/all.yaml
```

Base configs (data streams, operators, schedules) are shared via `trial_sources/base/{sport}.yaml`.

### Common SLS/OSS Endpoints

| Region | SLS Endpoint | OSS Endpoint |
|--------|--------------|--------------|
| China (Hangzhou) | `cn-hangzhou.log.aliyuncs.com` | `oss-cn-hangzhou.aliyuncs.com` |
| China (Shanghai) | `cn-shanghai.log.aliyuncs.com` | `oss-cn-shanghai.aliyuncs.com` |
| China (Wulanchabu) | `cn-wulanchabu.log.aliyuncs.com` | `oss-cn-wulanchabu.aliyuncs.com` |

---

## Operations

### View Logs

```bash
# Live logs (Docker handles rotation: 5 files x 100MB per container)
docker logs dojozero-nba -f
docker logs dojozero-nfl -f
docker logs dojozero-ncaa -f

# Recent logs
docker logs dojozero-nba --tail 100

# Logs since timestamp
docker logs dojozero-nba --since 2025-01-20T10:00:00
```

### Restart / Update

```bash
# Restart (after .env changes)
deploy/up.sh

# Update code and rebuild
cd DojoZero
git pull
deploy/up.sh --build

# Full rebuild (clear cache)
docker compose -f deploy/docker-compose.yml build --no-cache
deploy/up.sh
```

### Stop

```bash
# Stop container (preserves volumes)
docker compose -f deploy/docker-compose.yml down

# Stop and remove volumes (full reset)
docker compose -f deploy/docker-compose.yml down -v
```

### Monitor Resources

```bash
# CPU/memory usage
docker stats dojozero-nba dojozero-nfl dojozero-ncaa

# Disk usage
docker system df
du -sh outputs/ data/
```

---

## Maintenance

### Clean Up Old Data

```bash
# Remove event files older than 30 days
find outputs/ -name "*.jsonl" -mtime +30 -delete

# Remove old log files
find data/ -name "*.log" -mtime +30 -delete

# Docker cleanup (unused images, containers, volumes)
docker system prune -f
```

### Backup

```bash
# Backup outputs directory
tar -czf backup-$(date +%Y%m%d).tar.gz outputs/

# Note: Event data is also backed up to OSS automatically when trials stop
```

### Check Trial Data

```bash
# List recent trial outputs
ls -lht outputs/ | head -20

# View specific trial events
cat outputs/2025-01-20/sched-nba-401810490-abc12345.jsonl | head -10
```

---

## Troubleshooting

### Container won't start

```bash
# Check logs for errors
docker logs dojozero-nba
docker logs dojozero-nfl
docker logs dojozero-ncaa

# Common issues:
# - Missing .env file or variables
# - Invalid API keys
# - Port 8001/8002/8003 already in use
# - Missing alicloud extras (need pip install 'dojozero[alicloud]')
```

### No trials scheduled

```bash
# Check if trial sources loaded
curl http://localhost:8001/api/trial-sources  # NBA
curl http://localhost:8002/api/trial-sources  # NFL
curl http://localhost:8003/api/trial-sources  # NCAA

# Check ESPN sync status
docker logs dojozero-nba | grep -i "sync\|espn\|schedule"

# Verify trial source configs exist
ls -la trial_sources/prod/
```

### Wrong tier loaded

```bash
# Check what tier the container is using
docker inspect dojozero-nba --format '{{json .Config.Cmd}}'

# Check container env
docker exec dojozero-nba env | grep DOJOZERO_ENV

# Fix: always use deploy/up.sh which sources .env properly
deploy/up.sh
```

### SLS/OSS connection issues

```bash
# Check endpoints match region
# SLS and OSS should be in same region for best performance
```

### Health check failing

```bash
# Check if servers are responding
curl -v http://localhost:8001/health  # NBA
curl -v http://localhost:8002/health  # NFL
curl -v http://localhost:8003/health  # NCAA

# Check containers are running
docker ps -a | grep dojozero

# Restart containers
deploy/up.sh
```

---

## Volume Mounts

| Host Path | Container Path | Purpose |
|-----------|----------------|---------|
| `./outputs` | `/app/outputs` | Trial event JSONL files (shared) |
| `./data` | `/app/data` | Local data cache (shared) |
| `./trial_sources` | `/app/trial_sources` | Trial source configs (read-only) |
| `dojozero-nba-schedules` | `/app/.dojozero` | NBA schedule state |
| `dojozero-nfl-schedules` | `/app/.dojozero` | NFL schedule state |
| `dojozero-ncaa-schedules` | `/app/.dojozero` | NCAA schedule state |

---

## Local Development

For development without Docker:

```bash
# Setup (installs Python deps via uv)
chmod +x deploy/setup.sh
./deploy/setup.sh

# Configure
cp .env.template .env
nano .env

# Run single trial
dojo0 run trial_params/nba-moneyline.yaml

# Run server locally
DOJOZERO_ENV=daily dojo0 serve --trial-source trial_sources/daily/nba.yaml
```
