# DojoZero Docker Image
# Single container for dashboard server with auto-scheduling via trial sources

# Use Alibaba Cloud mirror for China, or docker.io for elsewhere
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# Use China apt mirror for faster builds (comment out if building outside China)
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Use China PyPI mirror for faster builds
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_TRUSTED_HOST=mirrors.aliyun.com
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Copy all source files needed for build
COPY pyproject.toml .
COPY README.md .
COPY src/ src/
COPY agents/ agents/
COPY trial_sources/ trial_sources/
COPY trial_params/ trial_params/

# Install dependencies and the package
RUN uv pip install --system .

# Create output directories
RUN mkdir -p outputs data

# Expose dashboard server port
EXPOSE 8000

# Health check - dashboard server health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command (override in docker-compose.yml for specific trial source)
CMD ["dojo0", "serve", "--host", "0.0.0.0", "--port", "8000"]
