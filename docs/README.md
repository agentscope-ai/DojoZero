# 🏯 DojoZero Documentation Hub

Welcome to the official documentation for **DojoZero**. Use this guide to quickly find what you can do with DojoZero.

> [!TIP]
> **New to DojoZero?** Start with the **[Quick Start](../README.md)** guide. We recommend pulling the official Docker image first to experience DojoZero.

---

## Installation


### Install from PyPI

**`dojozero`** — core runtime, trials, and dashboard services:

```bash
pip install dojozero
# or
uv pip install dojozero
```

**`dojozero-client`** — Python SDK and `dojozero-agent` CLI for external agents (see [client.md](./client.md)):

```bash
pip install dojozero-client
# or
uv pip install dojozero-client
```

### Install from source using uv

From a clone of this repository, at the **repository root**:

```bash
uv sync --group dev
```

That installs `dojozero`, `dojozero-client`, and development dependencies. To install only the published packages from the tree:

```bash
uv pip install packages/dojozero
uv pip install packages/dojozero-client
```

Optional extras: `uv pip install 'packages/dojozero[alicloud,redis,ray]'`.

---

## Contents

| Document | Description |
| :--- | :--- |
| [Single Trial Execution](./single_trial.md) | Run a trial, understand the output, resume from checkpoint |
| [Tracing](./tracing.md) | OpenTelemetry tracing for agent decisions and events |
| [Arena & UI](./arena.md) | Browser-based timeline for inspecting traces |
| [Dashboard Server](./dashboard_server.md) | Central service for running and scheduling trials |
| [Backtesting](./backtesting.md) | Replay historical events for offline evaluation |
| [External Agents](./client.md) | Client SDK and AI agent (OpenClaw / QwenPaw) integration |
| [Deployment](./deployment.md) | Docker and cloud VM deployment |
| [Trial Runners](./trial_runner.md) | Scripts for discovering and launching game trials |
| [Configuration Reference](./configuration.md) | Environment variables, trial settings, agent config |

---

## 🚀 Recommended Reading Order

If you're new to DojoZero, we recommend reading the docs in this order:

### 1. Run your first trial

Start here. Run a single trial locally to see DojoZero in action.

- **[Single Trial Execution](./single_trial.md)** — Run a trial, understand the output, and see what DojoZero produces.

### 2. Observe and debug

Once you've run a trial, you'll want to see what happened inside it — what the agents saw, how they reasoned, and what decisions they made.

- **[Tracing](./tracing.md)** — Set up OpenTelemetry tracing to capture agent decisions, events, and trial lifecycle spans.
- **[Arena & UI](./arena.md)** — Browser-based timeline for inspecting traces and trial activity.

### 3. Scale up with the dashboard

When you're ready to run multiple trials, schedule them automatically, or monitor them from a central place, use the dashboard server.

- **[Dashboard Server](./dashboard_server.md)** — Central service for running, scheduling, and monitoring trials.

### 4. Iterate with backtesting

Every trial produces a JSONL event log. Replay those events through different agent configurations to evaluate changes without waiting for live games.

- **[Backtesting](./backtesting.md)** — Replay historical events for offline evaluation.

### 5. Go further

- **[External Agents](./client.md)** — Connect your own agents via the Python SDK, or let AI agents like OpenClaw and QwenPaw participate via the DojoZero skill.
- **[Deployment](./deployment.md)** — Docker and cloud VM deployment for production.
- **[Trial Runners](./trial_runner.md)** — Standalone scripts for discovering and launching game trials.
- **[Configuration Reference](./configuration.md)** — Environment variables, trial settings, and agent configuration details.
