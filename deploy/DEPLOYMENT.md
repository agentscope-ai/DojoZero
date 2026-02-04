# DojoZero - Deployment Guide

## Quick Start (Docker)

```bash
# 1. Configure environment
cp deploy/.env.template .env
nano .env  # Fill in API keys and credentials

# 2. Build and run
docker-compose -f deploy/docker-compose.yml up -d

# 3. Verify
docker logs dojozero-nba --tail 50
docker logs dojozero-nfl --tail 50
curl http://localhost:8001/health  # NBA
curl http://localhost:8002/health  # NFL
```

Two containers run separately for NBA (port 8001) and NFL (port 8002). Each automatically discovers games from ESPN and schedules trials. No cron needed.

---

## Architecture

```
┌───────────────────────────────┐    ┌───────────────────────────────┐
│  dojozero-nba (port 8001)     │    │  dojozero-nfl (port 8002)     │
│                               │    │                               │
│  dojo0 serve                  │    │  dojo0 serve                  │
│    --trial-source nba.yaml    │    │    --trial-source nfl.yaml    │
│                               │    │                               │
│  ┌─────────────────────────┐  │    │  ┌─────────────────────────┐  │
│  │  ScheduleManager        │  │    │  │  ScheduleManager        │  │
│  │  - ESPN sync (hourly)   │  │    │  │  - ESPN sync (hourly)   │  │
│  │  - Game discovery       │  │    │  │  - Game discovery       │  │
│  │  - Trial lifecycle      │  │    │  │  - Trial lifecycle      │  │
│  └─────────────────────────┘  │    │  └─────────────────────────┘  │
│             │                 │    │             │                 │
│             ▼                 │    │             ▼                 │
│  ┌────────┬────────┬───────┐  │    │  ┌────────┬────────┬───────┐  │
│  │  SLS   │  OSS   │ JSONL │  │    │  │  SLS   │  OSS   │ JSONL │  │
│  └────────┴────────┴───────┘  │    │  └────────┴────────┴───────┘  │
└───────────────────────────────┘    └───────────────────────────────┘
                │                                │
                └────────────┬───────────────────┘
                             ▼
                     Shared volumes:
                     - ./outputs/
                     - ./data/
                     - .env
```

Two separate containers run NBA and NFL independently.

---

## Cloud VM Deployment (ECS/EC2)

### Setup Script

```bash
# SSH into your server
ssh user@your-server-ip

# Clone the repo
git clone https://github.com/your-org/DojoZero.git
cd DojoZero

# Run setup script (installs Docker, auto-detects China and configures mirrors)
chmod +x deploy/setup.sh
./deploy/setup.sh --docker

# Log out and back in for docker group (if Docker was just installed)
exit
# SSH back in

# Configure environment
cd DojoZero
nano .env  # Fill in credentials

# Build and run
docker-compose -f deploy/docker-compose.yml up -d
```

The setup script automatically:
- Installs Docker and docker-compose if missing
- Detects if Docker Hub is unreachable (China) and configures mirrors
- Creates .env from template

### Verify Deployment

```bash
# Check container status
docker ps

# View logs
docker logs dojozero-nba --tail 100
docker logs dojozero-nfl --tail 100

# Health checks
curl http://localhost:8001/health  # NBA
curl http://localhost:8002/health  # NFL

# Check scheduled trials
curl http://localhost:8001/api/schedules  # NBA
curl http://localhost:8002/api/schedules  # NFL
```

---

## Configuration

### Environment Variables

Copy `deploy/.env.template` to `.env` and fill in:

| Variable | Required | Description |
|----------|----------|-------------|
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

Edit `trial_sources/nba.yaml` or `trial_sources/nfl.yaml`:

```yaml
# Schedule options
pre_start_hours: 0.1           # Start 6 minutes before game
sync_interval_seconds: 3600.0  # Sync with ESPN every hour
check_interval_seconds: 60.0   # Check game status every minute
auto_stop_on_completion: true  # Stop when game ends
```

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

# Recent logs
docker logs dojozero-nba --tail 100
docker logs dojozero-nfl --tail 100

# Logs since timestamp
docker logs dojozero-nba --since 2025-01-20T10:00:00
```

### Restart / Update

```bash
# Restart (after .env changes)
docker compose -f deploy/docker-compose.yml restart

# Update code and rebuild
cd DojoZero
git pull
docker compose -f deploy/docker-compose.yml up -d --build

# Full rebuild (clear cache)
docker compose -f deploy/docker-compose.yml build --no-cache
docker compose -f deploy/docker-compose.yml up -d
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
docker stats dojozero-nba dojozero-nfl

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
cat outputs/2025-01-20/401810490.jsonl | head -10
```

---

## Troubleshooting

### Container won't start

```bash
# Check logs for errors
docker logs dojozero-nba
docker logs dojozero-nfl

# Common issues:
# - Missing .env file or variables
# - Invalid API keys
# - Port 8001/8002 already in use
```

### No trials scheduled

```bash
# Check if trial sources loaded
curl http://localhost:8001/api/trial-sources  # NBA
curl http://localhost:8002/api/trial-sources  # NFL

# Check ESPN sync status
docker logs dojozero-nba | grep -i "sync\|espn\|schedule"
docker logs dojozero-nfl | grep -i "sync\|espn\|schedule"

# Verify trial source configs exist
ls -la trial_sources/
```

### SLS/OSS connection issues

```bash
# Validate credentials
python tools/validate_alicloud_access.py --verbose

# Check endpoints match region
# SLS and OSS should be in same region for best performance
```

### Health check failing

```bash
# Check if servers are responding
curl -v http://localhost:8001/health  # NBA
curl -v http://localhost:8002/health  # NFL

# Check containers are running
docker ps -a | grep dojozero

# Restart containers
docker-compose -f deploy/docker-compose.yml restart
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
dojo0 serve --trial-source trial_sources/nba.yaml
```
