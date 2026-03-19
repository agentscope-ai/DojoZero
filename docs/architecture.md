# Architecture and Design Decisions

This document is an overview of core DojoZero architecture decisions.

## System At a Glance

DojoZero is an actor-based, event-driven system for running autonomous agents on live and replayable data:

- **Data Streams** ingest and publish domain events
- **Operators** manage synchronous stateful actions (for example, betting broker tools)
- **Agents** consume events, reason, and invoke operators
- **DataHub** routes and persists events for replay/backtesting
- **Dashboard Server** orchestrates trial lifecycle
- **Arena Server + UI** read traces and present live/replay views

## Foundational Decisions

### 1) Actor model and trial boundaries

- Data Streams, Operators, and Agents are actor-like components with lifecycle and state.
- A **trial** is the bounded runtime unit that wires these actors together.
- Trial state is checkpointable and resumable.

### 2) Event sourcing and replay

From data infrastructure and trace design:
- Event flow is centralized through DataHub.
- Events are persisted (JSONL) to support deterministic replay/backtesting.
- Push events are first-class replay artifacts; pull snapshots are live-only helpers.

### 3) Separation of concerns

The architecture separates:
- **External API adapters** (remote IO)
- **Data stores/processors** (poll + transform raw events)
- **DataHub** (delivery + persistence)
- **Operators** (domain state and side effects)
- **Agent logic** (reasoning and decision making)

This keeps the system extensible by sport, market type, and model provider.

## Data and Model Evolution

### Unified data model overhaul

A dedicated overhaul introduced:
- Shared typed identity models (team/player/venue/odds)
- Clear event hierarchy across NBA/NFL and pre-game insights
- Better typing in server/API boundaries to reduce ad-hoc dict assembly

Result: less schema drift, stronger contracts, and cleaner downstream UI processing.

### Betting broker design

Broker operator decisions include:
- Single-event interaction model exposed through tools (`get_event`, place/cancel bets, stats)
- Support for moneyline, spread, and totals
- Market and limit order handling with settlement lifecycle
- Operator-level tool allowlist to constrain agent capabilities

## Observability and APIs

### OpenTelemetry-first tracing

DojoZero treats spans as the canonical observability and replay-friendly metadata format:
- Trial lifecycle spans (`trial.started`, `trial.stopped`)
- Registration spans (`agent.registered`, `datastream.registered`)
- Event/message spans (`agent.input`, `agent.response`, domain event types)

Arena can reconstruct timeline and actor views from traces.

### Dashboard and Arena decoupling

Key API decision:
- **Dashboard (`dojo0 serve`)** manages trials and emits traces
- **Arena (`dojo0 arena`)** reads from trace backend and serves browser clients
- Data plane is trace-centric, keeping UI runtime independent from trial orchestration internals

### Background cache refresh in Arena

A later design introduces proactive cache warming:
- periodic refresh of REST-derived aggregates
- incremental live trial span caching
- replay cache for completed trials

Goal: stable low-latency UX and lower trace-store burst load.

## External and Multi-Agent Extensions

### External agent API

Design supports third-party agents via HTTP APIs (REST + SSE):
- per-trial gateway model
- dashboard proxy mode for multi-trial routing
- shard-based scaling for operational isolation

This allows external agents in any language to join trials without embedding into the runtime process.

### Social media data stream

Pregame social intelligence was introduced with:
- curated watchlist-based account tracking
- X API integration
- per-account summarization and relevance filtering into compact agent-consumable signals

This expands non-box-score signal coverage for agent decision making.

## Current Architecture Priorities

Based on design trajectory, current priorities are:

1. Keep trial execution deterministic and resumable.
2. Preserve clean boundaries between orchestration, data ingestion, and UI.
3. Increase observability via standardized traces and typed events.
4. Improve extensibility for external agents, additional markets, and richer data signals.

