#!/bin/bash
# DojoZero Setup Script
#
# Usage:
#   ./deploy/setup.sh                 # Local development (Python + uv)
#   ./deploy/setup.sh --docker        # Production (Docker, international)
#   ./deploy/setup.sh --docker --china # Production (Docker, China mirrors)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Parse arguments
DOCKER_MODE=false
CHINA_MIRRORS=false
for arg in "$@"; do
    case $arg in
        --docker|--production)
            DOCKER_MODE=true
            ;;
        --china)
            CHINA_MIRRORS=true
            ;;
    esac
done

echo "=========================================="
if [ "$DOCKER_MODE" = true ]; then
    if [ "$CHINA_MIRRORS" = true ]; then
        echo "DojoZero - Production Setup (Docker + China mirrors)"
    else
        echo "DojoZero - Production Setup (Docker)"
    fi
else
    echo "DojoZero - Development Setup (Python)"
fi
echo "=========================================="
echo ""

cd "$PROJECT_ROOT"

# =============================================================================
# Docker Mode (Production / ECS)
# =============================================================================
if [ "$DOCKER_MODE" = true ]; then

    # Install Docker if not present
    if ! command -v docker &> /dev/null; then
        echo "Installing Docker..."
        sudo apt-get update
        sudo apt-get install -y docker.io
        sudo systemctl enable docker
        sudo systemctl start docker
        sudo usermod -aG docker $USER
        echo "Docker installed"
        NEED_RELOGIN=true
    else
        echo "Docker already installed"
    fi

    # Install docker-compose if not present
    if ! command -v docker-compose &> /dev/null; then
        echo "Installing docker-compose..."
        sudo curl -L "https://github.com/docker/compose/releases/download/v2.24.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        sudo chmod +x /usr/local/bin/docker-compose
        echo "docker-compose installed"
    else
        echo "docker-compose already installed"
    fi

    # Configure Docker daemon mirrors for China if --china flag is set
    if [ "$CHINA_MIRRORS" = true ]; then
        echo ""
        echo "Configuring Docker daemon with China mirrors..."
        sudo mkdir -p /etc/docker
        sudo tee /etc/docker/daemon.json > /dev/null <<EOF
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io"
  ]
}
EOF
        sudo systemctl daemon-reload
        sudo systemctl restart docker
        echo "Docker configured with DaoCloud mirror"
    fi

    # Create directories
    mkdir -p outputs data

    # Setup .env
    if [ ! -f .env ]; then
        if [ -f deploy/.env.template ]; then
            cp deploy/.env.template .env
            echo ""
            echo "Created .env from template"
        fi
    else
        echo ".env already exists"
    fi

    echo ""
    echo "=========================================="
    echo "Setup complete!"
    echo "=========================================="
    echo ""
    if [ "$NEED_RELOGIN" = true ]; then
        echo "IMPORTANT: Log out and back in for docker group, then:"
        echo ""
    fi
    echo "Next steps:"
    echo "  1. Edit credentials: nano .env"
    if [ "$CHINA_MIRRORS" = true ]; then
        echo "  2. Deploy: CHINA_MIRRORS=true docker-compose -f deploy/docker-compose.yml up -d --build"
    else
        echo "  2. Deploy: docker-compose -f deploy/docker-compose.yml up -d --build"
    fi
    echo "  3. Verify: docker logs dojozero-nba --tail 50"
    echo "             docker logs dojozero-nfl --tail 50"
    echo ""
    exit 0
fi

# =============================================================================
# Development Mode (Python + uv)
# =============================================================================

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
uv pip install .

echo "Dependencies installed"

# Create output directories
echo ""
echo "Setting up directories..."
mkdir -p outputs data
echo "Directories created"

# Check for .env file
echo ""
if [ ! -f .env ]; then
    if [ -f deploy/.env.template ]; then
        cp deploy/.env.template .env
        echo "Created .env from template"
        echo ""
        echo "IMPORTANT: Edit .env and fill in your API keys:"
        echo "  nano .env"
    else
        echo "WARNING: deploy/.env.template not found"
    fi
else
    echo ".env file found"
fi

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys: nano .env"
echo "  2. Run single trial: dojo0 run trial_params/nba-moneyline.yaml"
echo ""
echo "  Or run server with auto-scheduling:"
echo "    dojo0 serve --trial-source trial_sources/nba.yaml"
echo ""
echo "  For production deployment:"
echo "    ./deploy/setup.sh --docker"
echo ""
