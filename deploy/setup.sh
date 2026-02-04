#!/bin/bash
# Setup script for DojoZero local development
# For production deployment, use Docker instead (see DEPLOYMENT.md)

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================="
echo "DojoZero - Local Development Setup"
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

echo "Python $PYTHON_VERSION found"

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

echo "uv found: $(uv --version)"

# Install the package and dependencies
echo ""
echo "Installing DojoZero package and dependencies..."
cd "$PROJECT_ROOT"
uv pip install .
uv pip install "python-dotenv" "tavily-python"

echo "Dependencies installed"

# Create output directories
echo ""
echo "Setting up directories..."
mkdir -p "$PROJECT_ROOT/outputs"
mkdir -p "$PROJECT_ROOT/data"
echo "Directories created"

# Check for .env file
echo ""
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "Creating .env.template..."
    cp "$PROJECT_ROOT/deploy/.env.template" "$PROJECT_ROOT/.env.template" 2>/dev/null || true
    echo ""
    echo "IMPORTANT: Copy .env.template to .env and fill in your API keys:"
    echo "  cp .env.template .env"
    echo "  nano .env"
else
    echo ".env file found"
fi

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "  For local development:"
echo "    dojo0 run trial_params/nba-moneyline.yaml"
echo ""
echo "  For production deployment:"
echo "    See deploy/DEPLOYMENT.md for Docker instructions"
echo ""
