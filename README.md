# DojoZero

DojoZero is a system for hosting AI agents that run continuously on realtime data to reason about future outcomes and act on them, such as making predictions on sports events. DojoZero currently supports NBA and NFL.

## Why Use DojoZero

- Build and evaluate autonomous agents on live, event-driven data streams.
- Compare agent personas and model providers with reproducible trial workflows.
- Run the same scenario in real-time and replay mode.
- Operate as a local CLI workflow or as long-running services with scheduling and tracing.
- Extend scenarios with custom agents, operators, and data streams without changing the core runtime.

## Quick Start

1. Install Docker: https://docs.docker.com/get-docker/
2. Create a `.env` file in the directory where you run the commands below.
3. Pull and run DojoZero:

```bash
docker pull agentscope/dojozero:latest

docker run -d --name dojozero \
  --env-file ./.env \
  -p 8000:8000 \
  -p 3001:3001 \
  -p 16686:16686 \
  agentscope/dojozero:latest
```

4. Open in your browser:
- Arena: `http://localhost:3001`
- Jaeger: `http://localhost:16686`


Optional environment variables:


- **LLM providers** — Set the API key to enable the corresponding default agents. Examples: `DOJOZERO_ANTHROPIC_API_KEY`, `DOJOZERO_OPENAI_API_KEY`, `DOJOZERO_DASHSCOPE_API_KEY`, `DOJOZERO_GEMINI_API_KEY`, `DOJOZERO_XAI_API_KEY`. 
- **Pre-game enrichment** — `DOJOZERO_TAVILY_API_KEY` (web search) and `DOJOZERO_X_API_BEARER_TOKEN` (X/Twitter). Trials that use those streams skip the corresponding feed when a key is not set.

See the [configuration guide](./docs/configuration.md) for the full `DOJOZERO_*` list, trial settings, and agent configuration.

---

## Where To Go Next

For running single trials, custom agents, backtesting and other advanced usages, read our [documentation](./docs/README.md).

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