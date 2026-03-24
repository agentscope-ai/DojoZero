# Installation

DojoZero splits Python dependencies into a **default (open-source) core** and **optional extras** so you can run Jaeger-based tracing and trials without installing Alibaba Cloud–specific wheels.

## 1. Default install (no Alibaba Cloud packages)

**Install**

```bash
uv pip install .
# or: pip install .
```

**What you get**

- CLI (`dojo0`), trial runs, dashboard server, arena server (with supported backends)
- OpenTelemetry + OTLP HTTP → **Jaeger** (`--trace-backend jaeger`)
- Core model/data integrations listed in `pyproject.toml` `dependencies` (DashScope, Tavily, etc.)

**What is not included**

- `oss2`, `alibabacloud-credentials`, `aliyun-log-python-sdk`, `redis`

So anything that needs OSS, Alibaba credentials, SLS, or the Redis client will fail at runtime with an install hint unless you add the extras below.

**Typical use**

- Local development and **Jaeger-only** tracing
- Running NBA/NFL trials with env keys from [configuration.md](./configuration.md)

---

## 2. Alibaba Cloud & Redis (optional extras)

**Install**

```bash
# Alibaba: OSS, SLS tracing / log access, credential chain used by those features
uv pip install '.[alicloud]'

# Redis: sync-service and other Redis-backed code paths
uv pip install '.[redis]'

# Both at once
uv pip install '.[alicloud,redis]'
```

**`[alicloud]` enables**

- OSS uploads and `dojo0 serve` OSS backup paths
- `oss://` style artifact/backtest paths where implemented
- `--trace-backend sls` and SLS-backed trace reading (CLI / servers)

**`[redis]` enables**

- Redis client usage in the sync-service and related features

**Other optional extra**

- **`[ray]`** — distributed execution via Ray (separate from Alibaba).

---

## Development / CI

Contributors use the dev dependency group so tests covering OSS, SLS, and Redis run without extra flags:

```bash
uv sync --group dev
```

That group pins the same packages as `[alicloud]` and `[redis]` for reproducible test runs.

---

## Environment variables

After install, copy `.env.example` → `.env` and set only what you need. Variables for OSS/SLS are documented there; they matter only when you use those features **and** have installed `[alicloud]`.

See also: [configuration.md](./configuration.md), [tracing.md](./tracing.md).
