#!/bin/bash
# Daily runner script for NBA Game Collector
# Wrapper script that runs the collector (which handles its own detailed logging)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration
DATA_DIR="${DATA_DIR:-$PROJECT_ROOT/data/nba-betting}"
DATE="${1:-$(date +%Y-%m-%d)}"  # Use provided date or today

# Change to project root
cd "$PROJECT_ROOT"

# Simple wrapper logging (collector handles its own detailed logging)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting NBA Game Collector for date: $DATE"

# Check prerequisites
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found in PATH" >&2
    exit 1
fi

if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "ERROR: .env file not found at $PROJECT_ROOT/.env" >&2
    echo "Please create .env file with required API keys (see .env.template)" >&2
    exit 1
fi

# Load environment variables (Python modules also load .env via load_dotenv, but this ensures they're available)
set -a
source "$PROJECT_ROOT/.env"
set +a

# Ensure data directory exists
mkdir -p "$DATA_DIR"

# Run with timeout (24 hours max)
TIMEOUT_SECONDS=$((24 * 60 * 60))
TIMEOUT_CMD=""
if command -v timeout &> /dev/null; then
    TIMEOUT_CMD="timeout"
elif command -v gtimeout &> /dev/null; then
    TIMEOUT_CMD="gtimeout"  # macOS with coreutils
fi

# Run collector (it handles its own logging)
if [ -n "$TIMEOUT_CMD" ]; then
    "$TIMEOUT_CMD" "$TIMEOUT_SECONDS" python3 "$PROJECT_ROOT/tools/nba_game_collector.py" \
        --data-dir "$DATA_DIR" \
        --date "$DATE" \
        --log-level INFO
    EXIT_CODE=$?
else
    python3 "$PROJECT_ROOT/tools/nba_game_collector.py" \
        --data-dir "$DATA_DIR" \
        --date "$DATE" \
        --log-level INFO
    EXIT_CODE=$?
fi

# Summary
if [ $EXIT_CODE -eq 0 ]; then
    if [ -d "$DATA_DIR/$DATE" ]; then
        GAME_COUNT=$(find "$DATA_DIR/$DATE" -name "*.jsonl" 2>/dev/null | wc -l | tr -d ' ')
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✓ Collection completed: $GAME_COUNT game(s)"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✓ Collection completed: No games found for $DATE"
    fi
    exit 0
elif [ $EXIT_CODE -eq 124 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Collection timed out after 24 hours" >&2
    exit 1
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Collection failed with exit code: $EXIT_CODE" >&2
    exit $EXIT_CODE
fi

