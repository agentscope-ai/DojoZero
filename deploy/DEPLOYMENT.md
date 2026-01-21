# Trial Runner - Deployment Guide

## Setup

1. Transfer project: `scp -r /path/to/DojoZero user@host:/path/` or `git clone`
2. Run setup: `cd DojoZero && chmod +x deploy/setup.sh && ./deploy/setup.sh` (creates `.env.template` if missing)
3. Configure: `cp .env.template .env && nano .env` (add `DOJOZERO_TAVILY_API_KEY`, `DOJOZERO_DASHSCOPE_API_KEY`, `DOJOZERO_PROXY_URL`, `DOJOZERO_POLY_PRIVATE_KEY`)
4. Test: `python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml`

## Usage

The script requires a config file and automatically detects trial type (NBA/NFL) from it:

```bash
# NBA trials
python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml
python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml --date 2025-01-20

# NFL trials
python deploy/run_daily_trials.py configs/nfl-game.yaml
python deploy/run_daily_trials.py configs/nfl-game.yaml --date 2025-01-20

# With OSS upload
python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml --oss-upload
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
# NBA trials at 6 AM:
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
- `--oss-upload` - Enable OSS upload
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

## OSS Upload (Optional)

Upload trial data to Alibaba Cloud OSS for centralized storage.

### Configuration

Add to `.env`:
```bash
# OSS credentials - handled by alibabacloud-credentials SDK
# Option 1: Environment variables
ALIBABA_CLOUD_ACCESS_KEY_ID=LTAI5t...
ALIBABA_CLOUD_ACCESS_KEY_SECRET=abc123...

# Option 2: Use ~/.alibabacloud/credentials file (see main README)

# OSS bucket configuration (required)
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

**Via daily runner:**
```bash
python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml --oss-upload
```

**Or directly with the trial runner:**
```bash
python tools/nba_trial_runner.py run --data-dir data/nba-betting --oss-upload
```

**Override bucket/prefix via CLI:**
```bash
python tools/nba_trial_runner.py run --data-dir data/nba-betting \
    --oss-upload --oss-bucket staging-bucket --oss-prefix test/
```

### OSS Structure

Files are uploaded mirroring the local structure:
```
{prefix}/nba/{date}/{game_id}.yaml
{prefix}/nba/{date}/{game_id}.jsonl
{prefix}/nba/{date}/{game_id}.log
```

### Cron with OSS

```bash
crontab -e
# Add:
0 6 * * * cd /path/to/DojoZero && python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml --data-dir data/nba-betting --oss-upload >> cron.log 2>&1
```

## Advanced

- Custom data dir: `python deploy/run_daily_trials.py configs/nba-pregame-betting.yaml --data-dir /custom/path`
- Multiple dates: Loop through dates with `--date` parameter
- Run both NBA and NFL: Set up two cron entries with different configs
