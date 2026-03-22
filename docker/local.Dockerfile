FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir uv

# Layer cache: third-party deps only (invalidates when pyproject.toml or uv.lock changes)
COPY pyproject.toml uv.lock README.md ./
RUN uv export --frozen --no-dev --no-emit-project --no-hashes --no-annotate \
        -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY src/ src/
COPY agents/ agents/
COPY trial_sources/ trial_sources/
COPY trial_params/ trial_params/

RUN uv pip install --system --no-cache .

RUN mkdir -p outputs data

ENV PYTHONUNBUFFERED=1
