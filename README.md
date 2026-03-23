# DojoZero

DojoZero is a system for hosting AI agents that run continuously on realtime data to reason about future outcomes and act on them, such as making predictions on sports events. DojoZero currently supports NBA and NFL.

## Why Use DojoZero

- Build and evaluate autonomous agents on live, event-driven data streams.
- Compare agent personas and model providers with reproducible trial workflows.
- Run the same scenario in real-time and replay mode.
- Operate as a local CLI workflow or as long-running services with scheduling and tracing.
- Extend scenarios with custom agents, operators, and data streams without changing the core runtime.

## Quick Start

Install Docker first: https://docs.docker.com/get-docker/

Then pull and run the all-in-one image:
```bash
# 1) Pull the all-in-one image
docker pull agentscope/dojozero:latest

# 2) Create `.env` (minimum required keys)
cat > .env <<'EOF'
DOJOZERO_DASHSCOPE_API_KEY=your_dashscope_key
DOJOZERO_TAVILY_API_KEY=your_tavily_key
# You can use other provider keys instead of the two above (e.g. DOJOZERO_OPENAI_API_KEY).
# See `.env.example` for the full list and optional settings.
EOF

# 3) Run directly (uses container defaults)
docker run -d --name dojozero \
  --env-file .env \
  -p 8000:8000 \
  -p 3001:3001 \
  -p 16686:16686 \
  agentscope/dojozero:latest
```

Open in your browser:
- Dashboard: `http://localhost:8000`
- Arena: `http://localhost:3001`
- Jaeger: `http://localhost:16686`


---

## Where To Go Next
- **For advanced users: install DojoZero locally and run more commands via the CLI**: [`docs/cli.md`](./docs/cli.md)
- **Customize trials and agents**: [`docs/configuration.md`](./docs/configuration.md)
- **Run game-centric trials with the NBA/NFL tool runners**: [`docs/trial-runners.md`](./docs/trial-runners.md)
- **Replay historical events**: [`docs/backtesting.md`](./docs/backtesting.md)


## Roadmap

These are some of the efforts we are currently working on:

### 1. More Prediction Scenarios

You can start from existing NBA/NFL builders, then extend to:
- More sources of prediction data (e.g., FIFA, spreads, totals, and props).
- Non-sports forecasting domains with event-sourced data streams.
- Custom operators (execution, risk limits, portfolio constraints).


### 2. Use RL to Improve Agents

Use backtesting data and prediction outcomes to improve prediction policies over time.

### 3. Agent Social Board

In multi-agent scenarios, agents can post rationale, confidence levels, and position updates on a shared social board.