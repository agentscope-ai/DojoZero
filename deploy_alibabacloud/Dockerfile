# DojoZero Docker Image
# Single container for dashboard server with auto-scheduling via trial sources

# Build args
ARG BASE_IMAGE=python:3.11-slim
ARG CHINA_MIRRORS=false

FROM ${BASE_IMAGE}

# Re-declare ARG after FROM (required for multi-stage)
ARG CHINA_MIRRORS

# Configure apt mirrors and install system dependencies
RUN if [ "$CHINA_MIRRORS" = "true" ]; then \
        echo "Configuring China apt mirrors..." && \
        sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources; \
    fi && \
    apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install uv (with China mirror if needed)
RUN if [ "$CHINA_MIRRORS" = "true" ]; then \
        pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com uv; \
    else \
        pip install --no-cache-dir uv; \
    fi

# Copy all source files needed for build
COPY pyproject.toml .
COPY README.md .
COPY src/ src/
COPY agents/ agents/
COPY trial_sources/ trial_sources/
COPY trial_params/ trial_params/

# Install dependencies and the package (with China mirror if needed)
RUN if [ "$CHINA_MIRRORS" = "true" ]; then \
        UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ uv pip install --system .; \
    else \
        uv pip install --system .; \
    fi

# Create output directories
RUN mkdir -p outputs data

# Expose dashboard server port
EXPOSE 8000

# Health check - dashboard server health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command (override in docker-compose.yml for specific trial source)
CMD ["dojo0", "serve", "--host", "0.0.0.0", "--port", "8000"]
