# NBA Game Collector - Deployment Guide

## Setup

1. Transfer project: `scp -r /path/to/DojoZero user@host:/path/` or `git clone`
2. Run setup: `cd DojoZero && chmod +x deploy/setup.sh && ./deploy/setup.sh` (creates `.env.template` if missing)
3. Configure: `cp .env.template .env && nano .env` (add `DOJOZERO_TAVILY_API_KEY`, `DOJOZERO_DASHSCOPE_API_KEY`, `DOJOZERO_PROXY_URL`, `DOJOZERO_POLY_PRIVATE_KEY`)
4. Test: `./deploy/run_daily.sh` or `./deploy/run_daily.sh 2025-12-20`

## Automation

**Cron (recommended):**

The setup script can configure cron for you:
```bash
./deploy/setup.sh  # Follow prompts to set up cron
```

Or manually:
```bash
crontab -e
# Add: 0 6 * * * /path/to/DojoZero/deploy/run_daily.sh >> /path/to/DojoZero/cron.log 2>&1
```

**Systemd (Linux):**
```ini
# /etc/systemd/system/nba-collector.service
[Unit]
Description=NBA Game Collector Daily Run
After=network.target
[Service]
Type=oneshot
User=your-username
WorkingDirectory=/path/to/DojoZero
ExecStart=/bin/bash /path/to/DojoZero/deploy/run_daily.sh
StandardOutput=append:/path/to/DojoZero/systemd.log
StandardError=append:/path/to/DojoZero/systemd_error.log

# /etc/systemd/system/nba-collector.timer
[Unit]
Description=Run NBA Collector Daily
Requires=nba-collector.service
[Timer]
OnCalendar=*-*-* 06:00:00
Persistent=true
[Install]
WantedBy=timers.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now nba-collector.timer
```

## Monitoring

```bash
# Per-game logs (created by collector)
tail -f data/nba-betting/$(date +%Y-%m-%d)/*.log

# Check collected games
ls -lh data/nba-betting/2025-12-17/
```

## Troubleshooting

- `python3 not found` → Install Python 3.11+, check PATH
- `DOJOZERO_TAVILY_API_KEY not set` or missing keys → Verify `.env` exists with all required keys (`chmod 600 .env`)
- `No games found` → Normal if no games scheduled
- `Collection timed out` → Increase timeout in `run_daily.sh`
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

## OSS Upload (Optional)

Upload collected data to Alibaba Cloud OSS for centralized storage.

### Configuration

Add to `.env`:
```bash
# OSS credentials (required for upload)
DOJOZERO_OSS_ACCESS_KEY_ID=LTAI5t...
DOJOZERO_OSS_ACCESS_KEY_SECRET=abc123...
DOJOZERO_OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
DOJOZERO_OSS_BUCKET=your-bucket-name
DOJOZERO_OSS_PREFIX=prod/  # Optional prefix for all keys
```

### Common Endpoints

| Region | Endpoint |
|--------|----------|
| China (Hangzhou) | `oss-cn-hangzhou.aliyuncs.com` |
| China (Shanghai) | `oss-cn-shanghai.aliyuncs.com` |
| China (Beijing) | `oss-cn-beijing.aliyuncs.com` |
| Singapore | `oss-ap-southeast-1.aliyuncs.com` |
| US (Virginia) | `oss-us-east-1.aliyuncs.com` |

### Usage

**Enable via environment variable:**
```bash
OSS_UPLOAD=true ./deploy/run_daily.sh
```

**Or directly with the collector:**
```bash
python tools/nba_game_collector.py --data-dir data/nba-betting --oss-upload
python tools/nfl_game_collector.py --data-dir data/nfl --oss-upload
```

**Override bucket/prefix via CLI:**
```bash
python tools/nba_game_collector.py --data-dir data/nba-betting \
    --oss-upload --oss-bucket staging-bucket --oss-prefix test/
```

### OSS Structure

Files are uploaded mirroring the local structure:
```
{prefix}/nba/{date}/{game_id}.yaml
{prefix}/nba/{date}/{game_id}.jsonl
{prefix}/nba/{date}/{game_id}.log

{prefix}/nfl/{date}/{event_id}.yaml
{prefix}/nfl/{date}/{event_id}.jsonl
{prefix}/nfl/{date}/{event_id}.log
```

### Cron with OSS

```bash
crontab -e
# Add:
0 6 * * * OSS_UPLOAD=true /path/to/DojoZero/deploy/run_daily.sh >> /path/to/DojoZero/cron.log 2>&1
```

## Advanced

- Custom data dir: `export DATA_DIR=/custom/path && ./deploy/run_daily.sh`
- Adjust timing: Edit `--pre-start-hours`/`--check-interval` in `run_daily.sh`
- Multiple dates: Loop through dates calling `run_daily.sh` for each
