# DojoZero Docker Image
# Single container for dashboard server with auto-scheduling via trial sources

# Use Alibaba Cloud mirror for China, or docker.io for elsewhere
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Copy dependency files first for better layer caching
COPY pyproject.toml .
COPY README.md .

# Install dependencies (no dev dependencies in production)
RUN uv pip install --system .

# Install additional runtime dependencies
RUN uv pip install --system python-dotenv tavily-python

# Copy application code
COPY src/ src/
COPY agents/ agents/
COPY trial_sources/ trial_sources/
COPY trial_params/ trial_params/

# Create output directories
RUN mkdir -p outputs data

# Expose dashboard server port
EXPOSE 8000

# Health check - dashboard server health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command (override in docker-compose.yml for specific trial source)
CMD ["dojo0", "serve", "--host", "0.0.0.0", "--port", "8000"]
