# Trial Runner - Deployment Guide

## Setup

1. Transfer project: `scp -r /path/to/DojoZero user@host:/path/` or `git clone`
2. Run setup: `cd DojoZero && chmod +x deploy/setup.sh && ./deploy/setup.sh` (creates `.env.template` if missing)
3. Configure: `cp .env.template .env && nano .env` (add `DOJOZERO_TAVILY_API_KEY`, `DOJOZERO_DASHSCOPE_API_KEY`, `DOJOZERO_PROXY_URL`, `DOJOZERO_POLY_PRIVATE_KEY`)
4. Test: `python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml`

## Usage

The script requires a config file and automatically detects trial type (NBA/NFL) from it:

```bash
# NBA trials (local mode)
python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml
python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml --date 2025-01-20

# NFL trials (local mode)
python deploy/run_daily_trials.py configs/nfl-game.yaml
python deploy/run_daily_trials.py configs/nfl-game.yaml --date 2025-01-20

# With Dashboard Server (SLS + OSS integration)
# Terminal 1: Start server
dojo0 serve --otlp-endpoint https://... --trace-backend sls --oss-backup

# Terminal 2: Run trials
python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml --server http://localhost:8000
```

## Automation

**Cron (recommended):**

The setup script can configure cron for you:
```bash
./deploy/setup.sh  # Follow prompts to set up cron
```

Or manually:
```bash
crontab -e
# NBA trials at 6 AM (local mode):
# 0 6 * * * cd /path/to/DojoZero && python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml --data-dir data/nba-betting >> cron.log 2>&1

# NFL trials at 10 AM:
# 0 10 * * * cd /path/to/DojoZero && python deploy/run_daily_trials.py configs/nfl-game.yaml --data-dir data/nfl >> cron_nfl.log 2>&1
```

**Systemd (Linux):**
```ini
# /etc/systemd/system/nba-trials.service
[Unit]
Description=NBA Betting Trials Daily Run
After=network.target
[Service]
Type=oneshot
User=your-username
WorkingDirectory=/path/to/DojoZero
ExecStart=/usr/bin/python3 deploy/run_daily_trials.py configs/nba-pregame-betting.yaml
StandardOutput=append:/path/to/DojoZero/systemd.log
StandardError=append:/path/to/DojoZero/systemd_error.log

# /etc/systemd/system/nba-trials.timer
[Unit]
Description=Run NBA Betting Trials Daily
Requires=nba-trials.service
[Timer]
OnCalendar=*-*-* 06:00:00
Persistent=true
[Install]
WantedBy=timers.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now nba-trials.timer
```

## Configuration

**CLI options:**
- `--data-dir` - Output directory (auto-detected from config if not specified)
- `--server` - Dashboard Server URL for SLS/OSS integration
- `--log-level` - DEBUG, INFO, WARNING, ERROR (default: INFO)
- `--timeout` - Timeout in seconds (default: 86400 = 24 hours)

**Example with custom data directory:**
```bash
python deploy/run_daily_trials.py configs/nfl-game.yaml --data-dir /custom/path --date 2025-01-20
```

## Monitoring

```bash
# Per-game logs (created by trial runner)
tail -f data/nba-betting/$(date +%Y-%m-%d)/*.log

# Check trial outputs
ls -lh data/nba-betting/2025-12-17/
```

## Troubleshooting

- `python3 not found` → Install Python 3.11+, check PATH
- `DOJOZERO_TAVILY_API_KEY not set` or missing keys → Verify `.env` exists with all required keys (`chmod 600 .env`)
- `No games found` → Normal if no games scheduled
- `Trials timed out` → Increase `--timeout` value
- Cron not running → Check `crontab -l`, service status, logs

## Maintenance

```bash
# Update
uv pip install --upgrade . "nba_api" "tavily-python" "dashscope"

# Clean old game logs (30+ days)
find data/nba-betting/ -name "*.log" -mtime +30 -delete

# Backup
tar -czf nba-betting-backup-$(date +%Y%m%d).tar.gz data/nba-betting/
```

## SLS + OSS Integration (Production)

For production deployments with trace export and backup, use the Dashboard Server.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    DASHBOARD SERVER                              │
│                    (dojo0 serve)                                 │
│                                                                  │
│   --otlp-endpoint  →  SLS trace export                          │
│   --trace-backend sls                                            │
│   --oss-backup     →  OSS backup on trial stop                  │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ --server http://localhost:8000
                              │
┌─────────────────────────────────────────────────────────────────┐
│                    TRIAL RUNNER                                  │
│              (deploy/run_daily_trials.py)                        │
│                                                                  │
│   Submits trials to Dashboard Server                             │
│   Local events JSONL still written to --data-dir                │
└─────────────────────────────────────────────────────────────────┘
```

### Configuration

Add to `.env`:
```bash
# SLS credentials - handled by alibabacloud-credentials SDK
ALIBABA_CLOUD_ACCESS_KEY_ID=LTAI5t...
ALIBABA_CLOUD_ACCESS_KEY_SECRET=abc123...

# SLS configuration
DOJOZERO_SLS_PROJECT=my-project
DOJOZERO_SLS_ENDPOINT=cn-hangzhou.log.aliyuncs.com
DOJOZERO_SLS_LOGSTORE=dojozero-traces

# OSS configuration (for backup)
DOJOZERO_OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
DOJOZERO_OSS_BUCKET=your-bucket-name
DOJOZERO_OSS_PREFIX=prod/  # Optional prefix for all keys
```

### Usage

**Start Dashboard Server (keep running):**
```bash
dojo0 serve \
  --otlp-endpoint https://my-project.cn-hangzhou.log.aliyuncs.com \
  --trace-backend sls \
  --oss-backup
```

**Run trials via cron:**
```bash
crontab -e
# Add:
0 6 * * * cd /path/to/DojoZero && python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml --data-dir data/nba-betting --server http://localhost:8000 >> cron.log 2>&1
```

### Common SLS/OSS Endpoints

| Region | SLS Endpoint | OSS Endpoint |
|--------|--------------|--------------|
| China (Hangzhou) | `cn-hangzhou.log.aliyuncs.com` | `oss-cn-hangzhou.aliyuncs.com` |
| China (Shanghai) | `cn-shanghai.log.aliyuncs.com` | `oss-cn-shanghai.aliyuncs.com` |
| China (Beijing) | `cn-beijing.log.aliyuncs.com` | `oss-cn-beijing.aliyuncs.com` |
| China (Wulanchabu) | `cn-wulanchabu.log.aliyuncs.com` | `oss-cn-wulanchabu.aliyuncs.com` |

### Data Flow

| Location | Description |
|----------|-------------|
| Local `{data-dir}/{date}/{game_id}.jsonl` | Always written |
| SLS | Real-time trace spans (if server has `--otlp-endpoint`) |
| OSS `trials/{trial_id}/events.jsonl` | Backup on trial stop (if server has `--oss-backup`) |

## Advanced

- Custom data dir: `python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml --data-dir /custom/path`
- Multiple dates: Loop through dates with `--date` parameter
- Run both NBA and NFL: Set up two cron entries with different configs
