# Architecture and Design Decisions

This document summarizes the current DojoZero architecture and core runtime behavior.

## System At a Glance

DojoZero is an actor-based, event-driven system for autonomous decision-making on live and replayable sports data:

- **Data Streams** ingest external data and publish typed events
- **Operators** expose stateful tools (for example, prediction actions)
- **Agents** consume events, reason with LLMs, and invoke operators
- **DataHub** centralizes routing, persistence, ordering, dedup, and replay
- **Dashboard Server** manages trial lifecycle and scheduling
- **Arena Server + UI** provide read-only live/replay visualization from traces

## Foundational Decisions

### 1) Actor model and trial boundary

- `Data Streams`, `Operators`, and `Agents` are actor-like components with lifecycle and state.
- A **trial** is the bounded runtime unit that wires these actors together.
- Trial state is checkpointable and resumable.

### 2) Event sourcing and replay

- Event flow is centralized through `DataHub`.
- Events are persisted to JSONL and used as replay inputs for backtesting.
- Replay is driven by `BacktestCoordinator` via `DataHub.start_backtest()/backtest_next()`.
- Game/odds events are primary replay artifacts; websearch/social events are live-fetched but are replayable once emitted and persisted.

## Actor Designs

### 1) Data Streams and event model

Typed events follow a clear hierarchy:

- `DataEvent` (base)
- `SportEvent` (adds sport/game identity)
  - `GameEvent`
    - Lifecycle: `GameInitializeEvent`, `GameStartEvent`, `GameResultEvent`
    - Atomic: `BasePlayEvent` -> NBA/NFL play events
    - Segment: `BaseSegmentEvent` -> `NFLDriveEvent`
    - Snapshot: `BaseGameUpdateEvent` -> NBA/NFL game update events
    - Odds: `OddsUpdateEvent`
  - `PreGameInsightEvent`
    - `WebSearchInsightEvent` (injury, ranking, expert prediction variants)
    - `StatsInsightEvent` (`PreGameStatsEvent`)
    - `TwitterTopTweetsEvent` (social signal)

### 2) Operators

`PredictionOperator` is the primary operator for prediction workflows:

- Maintains agent accounts, balances, holdings, and prediction history
- Tracks single-event market lifecycle (scheduled/live/closed/settled) and real-time odds updates
- Handles order placement, execution, and settlement
- Supports per-trial tool allowlists to constrain agent capabilities and enable cleaner agent-performance comparison

### 3) Agents (ReAct orchestration)

`PredictionAgent` wraps an internal ReAct agent and adds runtime controls:

- Subscribes to stream events and invokes registered operators
- Formats typed events into model-ready context
- Queues events while busy and consolidates backlog
- Maintains memory with compression for long event sequences
- Emits input/response spans for observability

## Services and APIs

### Dashboard Server (`dojo0 serve`)

- Hosts trial management APIs (launch/stop/status/results)
- Manages scheduling and trial source registration
- Can launch replay/backtest-mode trials from persisted JSONL
- Generates safe server-side persistence paths for trial data
- Integrates tracing export for managed trials

### Arena Server (`dojo0 arena`)

- Read-only service built on trace data (with internal caching; no trial orchestration)
- Provides REST + WebSocket endpoints for live stream and replay views
- Builds trial timelines, actor actions, leaderboard/stats, and replay payloads
- Uses replay/cache layers to reduce repeated trace-store queries

## Observability

### OpenTelemetry-based tracing

DojoZero treats spans as canonical observability metadata:

- Trial lifecycle spans (`trial.started`, `trial.stopped`)
- Registration spans (`agent.registered`, `datastream.registered`)
- Agent message spans (`agent.input`, `agent.response`)
- Prediction spans (`prediction.prediction`, `prediction.final_stats`, plus state-change spans)
- Data event spans from `DataHub`

