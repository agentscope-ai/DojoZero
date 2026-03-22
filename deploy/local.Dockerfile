FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
COPY README.md .
COPY src/ src/
COPY agents/ agents/
COPY trial_sources/ trial_sources/
COPY trial_params/ trial_params/

RUN uv pip install --system .

RUN mkdir -p outputs data

ENV PYTHONUNBUFFERED=1
