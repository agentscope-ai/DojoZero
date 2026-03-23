FROM python:3.11-slim

ARG JAEGER_VERSION=2.16.0
ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    supervisor \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir uv

RUN case "${TARGETARCH}" in \
      amd64) JAEGER_ARCH=amd64 ;; \
      arm64) JAEGER_ARCH=arm64 ;; \
      *) echo "Unsupported TARGETARCH: ${TARGETARCH}" && exit 1 ;; \
    esac \
    && curl -fsSL "https://github.com/jaegertracing/jaeger/releases/download/v${JAEGER_VERSION}/jaeger-${JAEGER_VERSION}-linux-${JAEGER_ARCH}.tar.gz" \
      | tar -xz -C /tmp \
    && mv "/tmp/jaeger-${JAEGER_VERSION}-linux-${JAEGER_ARCH}/jaeger" /usr/local/bin/jaeger \
    && chmod +x /usr/local/bin/jaeger \
    && ln -sf /usr/local/bin/jaeger /usr/local/bin/jaeger-all-in-one \
    && rm -rf "/tmp/jaeger-${JAEGER_VERSION}-linux-${JAEGER_ARCH}"

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
COPY docker/allinone/entrypoint.sh /app/docker/allinone/entrypoint.sh
COPY docker/allinone/supervisord.full.conf /app/docker/allinone/supervisord.full.conf

RUN uv pip install --system --no-cache . \
    && chmod +x /app/docker/allinone/entrypoint.sh

RUN mkdir -p outputs data

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/allinone/entrypoint.sh"]
