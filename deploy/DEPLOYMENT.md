# NBA Game Collector - Deployment Guide

## Setup

1. Transfer project: `scp -r /path/to/AgentX user@host:/path/` or `git clone`
2. Run setup: `cd AgentX && chmod +x deploy/setup.sh && ./deploy/setup.sh` (creates `.env.template` if missing)
3. Configure: `cp .env.template .env && nano .env` (add `TAVILY_API_KEY`, `DASHSCOPE_API_KEY`, `PROXY_URL`, `POLY_PRIVATE_KEY`)
4. Test: `./deploy/run_daily.sh` or `./deploy/run_daily.sh 2025-12-20`

## Automation

**Cron (recommended):**
```bash
crontab -e
# Add: 0 6 * * * /path/to/AgentX/deploy/run_daily.sh >> /path/to/AgentX/cron.log 2>&1
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
WorkingDirectory=/path/to/AgentX
ExecStart=/bin/bash /path/to/AgentX/deploy/run_daily.sh
StandardOutput=append:/path/to/AgentX/systemd.log
StandardError=append:/path/to/AgentX/systemd_error.log

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

- `python3 not found` → Install Python 3.10+, check PATH
- `TAVILY_API_KEY not set` or missing keys → Verify `.env` exists with all required keys (`chmod 600 .env`)
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

## Advanced

- Custom data dir: `export DATA_DIR=/custom/path && ./deploy/run_daily.sh`
- Adjust timing: Edit `--pre-start-hours`/`--check-interval` in `run_daily.sh`
- Multiple dates: Loop through dates calling `run_daily.sh` for each
