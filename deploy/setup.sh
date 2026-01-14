#!/bin/bash
# Setup script for deploying DojoZero NBA Game Collector to a Unix machine
# This script sets up the Python environment, installs dependencies, and prepares the system

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================="
echo "DojoZero NBA Game Collector - Setup"
echo "=========================================="
echo ""

# Check Python version
echo "Checking Python version..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 is not installed. Please install Python 3.11 or higher."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]); then
    echo "ERROR: Python 3.11+ is required. Found: $PYTHON_VERSION"
    exit 1
fi

echo "✓ Python $PYTHON_VERSION found"

# Check/install uv
echo ""
echo "Checking for uv package manager..."
if ! command -v uv &> /dev/null; then
    echo "uv not found. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
    if ! command -v uv &> /dev/null; then
        echo "ERROR: Failed to install uv. Please install manually from https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi
fi

echo "✓ uv found: $(uv --version)"

# Create virtual environment (optional, but recommended)
echo ""
echo "Setting up Python environment..."
cd "$PROJECT_ROOT"

# Install the package and dependencies
echo "Installing DojoZero package and dependencies..."
uv pip install .

# Install dev dependencies (includes nba_api, tavily-python, etc.)
echo "Installing additional dependencies for NBA collector..."
uv pip install "nba_api" "python-dotenv" "tavily-python" "dashscope" "py-clob-client"

echo "✓ Dependencies installed"

# Create data directory if it doesn't exist
echo ""
echo "Setting up data directory..."
DATA_DIR="${DATA_DIR:-$PROJECT_ROOT/data/nba-betting}"
mkdir -p "$DATA_DIR"
echo "✓ Data directory: $DATA_DIR"

# Check for .env file
echo ""
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "WARNING: .env file not found. Creating template..."
    cat > "$PROJECT_ROOT/.env.template" << 'EOF'
# DojoZero Environment Variables
# Copy this file to .env and fill in your API keys

# Tavily API key for web search
DOJOZERO_TAVILY_API_KEY=your_tavily_api_key_here

# Dashscope API key for LLM calls
DOJOZERO_DASHSCOPE_API_KEY=your_dashscope_api_key_here

# Proxy URL for NBA API requests
DOJOZERO_PROXY_URL=http://proxy.example.com:8080

# Polymarket private key for CLOB authentication
DOJOZERO_POLY_PRIVATE_KEY=0x...

# OSS (Alibaba Cloud Object Storage) - for uploading collected data
# Leave empty to disable OSS upload
DOJOZERO_OSS_ACCESS_KEY_ID=
DOJOZERO_OSS_ACCESS_KEY_SECRET=
DOJOZERO_OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
DOJOZERO_OSS_BUCKET=
DOJOZERO_OSS_PREFIX=
EOF
    echo "✓ Created .env.template - please copy to .env and fill in your API keys"
else
    echo "✓ .env file found"
fi

# Make scripts executable
echo ""
echo "Making scripts executable..."
chmod +x "$PROJECT_ROOT/deploy/run_daily.sh"
chmod +x "$PROJECT_ROOT/tools/nba_game_collector.py"
echo "✓ Scripts are executable"

# Cron job setup (interactive)
echo ""
echo "=========================================="
echo "Cron Job Setup (Optional)"
echo "=========================================="
echo ""
read -p "Would you like to set up a daily cron job? [y/N] " SETUP_CRON

if [[ "$SETUP_CRON" =~ ^[Yy]$ ]]; then
    # Get cron time
    echo ""
    echo "What time should the collector run daily?"
    echo "  - NBA games typically start between 7 PM - 10 PM ET"
    echo "  - Recommended: Run early morning to catch all games for the day"
    read -p "Enter hour (0-23) [default: 6]: " CRON_HOUR
    CRON_HOUR="${CRON_HOUR:-6}"
    read -p "Enter minute (0-59) [default: 0]: " CRON_MINUTE
    CRON_MINUTE="${CRON_MINUTE:-0}"

    # Ask about OSS upload
    echo ""
    read -p "Enable OSS upload for collected data? [y/N] " ENABLE_OSS
    if [[ "$ENABLE_OSS" =~ ^[Yy]$ ]]; then
        OSS_ENV="OSS_UPLOAD=true "
        echo "  OSS upload will be enabled. Make sure OSS credentials are configured in .env"
    else
        OSS_ENV=""
    fi

    # Build cron entry
    LOG_FILE="$PROJECT_ROOT/cron.log"
    CRON_ENTRY="$CRON_MINUTE $CRON_HOUR * * * ${OSS_ENV}$PROJECT_ROOT/deploy/run_daily.sh >> $LOG_FILE 2>&1"

    echo ""
    echo "The following cron entry will be added:"
    echo "  $CRON_ENTRY"
    echo ""
    read -p "Proceed with adding this cron job? [y/N] " CONFIRM_CRON

    if [[ "$CONFIRM_CRON" =~ ^[Yy]$ ]]; then
        # Check if entry already exists
        EXISTING_CRON=$(crontab -l 2>/dev/null || true)
        if echo "$EXISTING_CRON" | grep -q "run_daily.sh"; then
            echo ""
            echo "WARNING: A cron entry for run_daily.sh already exists:"
            echo "$EXISTING_CRON" | grep "run_daily.sh"
            echo ""
            read -p "Replace existing entry? [y/N] " REPLACE_CRON
            if [[ "$REPLACE_CRON" =~ ^[Yy]$ ]]; then
                # Remove existing entry and add new one
                (echo "$EXISTING_CRON" | grep -v "run_daily.sh"; echo "$CRON_ENTRY") | crontab -
                echo "✓ Cron job updated"
            else
                echo "Skipping cron setup (existing entry preserved)"
            fi
        else
            # Add new entry
            (crontab -l 2>/dev/null || true; echo "$CRON_ENTRY") | crontab -
            echo "✓ Cron job added"
        fi

        echo ""
        echo "Current crontab:"
        crontab -l | grep "run_daily.sh" || echo "  (no matching entries)"
    else
        echo "Skipping cron setup"
    fi
else
    echo "Skipping cron setup. You can set it up later - see deploy/DEPLOYMENT.md"
fi

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "1. Copy .env.template to .env and fill in your API keys:"
    echo "   cp $PROJECT_ROOT/.env.template $PROJECT_ROOT/.env"
    echo "   # Edit .env with your API keys"
    echo ""
    echo "2. Test the collector manually:"
    echo "   $PROJECT_ROOT/deploy/run_daily.sh"
else
    echo "1. Test the collector manually:"
    echo "   $PROJECT_ROOT/deploy/run_daily.sh"
fi
echo ""
echo "For more options, see deploy/DEPLOYMENT.md"
echo ""


