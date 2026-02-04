#!/bin/bash
# Setup script for Alibaba Cloud ECS deployment
# Run with: curl -fsSL <raw-url> | bash
# Or: chmod +x deploy/setup-ecs.sh && ./deploy/setup-ecs.sh

set -e

echo "=========================================="
echo "DojoZero - ECS Setup"
echo "=========================================="

# Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    sudo apt-get update
    sudo apt-get install -y docker.io
    sudo systemctl enable docker
    sudo systemctl start docker
    sudo usermod -aG docker $USER
    echo "Docker installed"
fi

# Install docker-compose if not present
if ! command -v docker-compose &> /dev/null; then
    echo "Installing docker-compose..."
    sudo curl -L "https://github.com/docker/compose/releases/download/v2.24.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
    echo "docker-compose installed"
fi

# Configure Docker mirror for China
echo "Configuring Docker mirrors for China..."
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json > /dev/null <<EOF
{
  "registry-mirrors": [
    "https://registry.cn-hangzhou.aliyuncs.com",
    "https://mirror.ccs.tencentyun.com"
  ]
}
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker

echo "Docker configured with China mirrors"

# Create directories
mkdir -p outputs data

# Check for .env
if [ ! -f .env ]; then
    if [ -f deploy/.env.template ]; then
        cp deploy/.env.template .env
        echo ""
        echo "Created .env from template. Please edit it:"
        echo "  nano .env"
    fi
fi

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Log out and back in (for docker group)"
echo "  2. Edit .env with your credentials: nano .env"
echo "  3. Deploy: docker-compose -f deploy/docker-compose.yml up -d"
echo ""
