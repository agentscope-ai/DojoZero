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
    echo "ERROR: python3 is not installed. Please install Python 3.10 or higher."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    echo "ERROR: Python 3.10+ is required. Found: $PYTHON_VERSION"
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
TAVILY_API_KEY=your_tavily_api_key_here

# Dashscope API key for LLM calls
DASHSCOPE_API_KEY=your_dashscope_api_key_here

# Proxy URL for NBA API requests
PROXY_URL=http://proxy.example.com:8080

# Polymarket private key for CLOB authentication
POLY_PRIVATE_KEY=0x...
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

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Copy .env.template to .env and fill in your API keys:"
echo "   cp $PROJECT_ROOT/.env.template $PROJECT_ROOT/.env"
echo "   # Edit .env with your API keys"
echo ""
echo "2. Test the collector manually:"
echo "   $PROJECT_ROOT/deploy/run_daily.sh"
echo ""
echo "3. Set up cron job for daily execution:"
echo "   See deploy/DEPLOYMENT.md for instructions"
echo ""


