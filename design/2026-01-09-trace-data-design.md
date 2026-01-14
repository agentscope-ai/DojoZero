# Trace Data Design

Unified span data format for trace storage and transmission, compatible with OpenTelemetry.

## Design Principles

- **OpenTelemetry Native**: All data flows through OTLP protocol to Jaeger
- **Unified Span Protocol**: All data (metadata + events) represented as spans
- **Real-time Export**: Spans exported synchronously via `OTelSpanExporter`

## Core Data Structure: SpanData

| Field | Type | Description |
|-------|------|-------------|
| trace_id | string | Trial ID (used as trace correlation ID) |
| span_id | string | Unique 16-character hex identifier |
| operation_name | string | Span type (see Span Types below) |
| start_time | int | Microseconds since epoch |
| duration | int | Microseconds (0 for instant events) |
| parent_span_id | string? | Parent span for hierarchy |
| tags | dict | Key-value metadata |
| logs | list | Span logs/events |

## Span Types

### 1. Trial Lifecycle Spans

Control the trial state machine and determine trial phase from traces.

| Operation Name | Description | When Emitted |
|----------------|-------------|--------------|
| `trial.started` | Trial has started running | `Dashboard.launch_trial()` completes |
| `trial.stopped` | Trial has stopped | `Dashboard.stop_trial()` completes |

**Special Tags:**
- `dojozero.trial.phase`: "started" or "stopped"
- `trial.final_phase`: Final phase value (stopped spans only)

### 2. Registration Spans (Resource Spans)

Emitted once per actor to describe its metadata. Frontend uses these to build the actor list.

| Operation Name | Description |
|----------------|-------------|
| `agent.registered` | Agent metadata (model, tools, system prompt) |
| `datastream.registered` | DataStream metadata (source type) |

### 3. DataStream Event Spans

Operation name is dynamic based on the event type from the data source.

| Example Operation Names | Source |
|------------------------|--------|
| `game_update` | NBA game state changes |
| `odds_update` | Betting odds changes |
| `score_change` | Score updates |
| `stream.event` | Generic fallback |

### 4. Agent Message Spans

Represent the agent conversation history.

| Operation Name | Role | Description |
|----------------|------|-------------|
| `agent.input` | user | Input from stream events |
| `agent.response` | assistant | LLM response |
| `agent.tool_result` | system | Tool execution result |

## Standard Tags

### Framework Tags (`dojozero.*`)

Present on all spans for filtering and correlation.

| Tag | Description |
|-----|-------------|
| `dojozero.trial.id` | Trial identifier |
| `dojozero.actor.id` | Actor unique identifier |
| `dojozero.actor.type` | Actor type: "agent" or "datastream" |
| `dojozero.event.type` | Event type (same as operationName) |
| `dojozero.event.sequence` | Event sequence number |
| `dojozero.trial.phase` | Trial phase (lifecycle spans only) |

### Resource Tags (`resource.*`)

Present only on registration spans (`*.registered`).

| Tag | Description |
|-----|-------------|
| `resource.name` | Display name for the actor |
| `resource.model` | Model identifier (agents only) |
| `resource.model_provider` | Provider: openai, anthropic, etc. (agents only) |
| `resource.system_prompt` | System prompt text (agents only) |
| `resource.tools` | Available tools as JSON array (agents only) |
| `resource.source_type` | Data source type (datastreams only) |

### Event Tags (`event.*`)

Present on event spans. Values are JSON-serialized for complex types.

| Tag | Description |
|-----|-------------|
| `event.stream_id` | Source stream ID |
| `event.role` | Message role: user, assistant, system |
| `event.name` | Agent or tool name |
| `event.content` | Message text content |
| `event.tool_calls` | Tool calls as JSON array |
| `event.tool_call_id` | Tool call ID (for tool results) |
| `event.message_id` | Message unique ID |
| `event.*` | Domain-specific data (e.g., `event.home_team`) |

## Architecture

### Export Path

1. Actors emit spans via `emit_span()` helper function
2. `OTelSpanExporter` wraps OpenTelemetry SDK with `SimpleSpanProcessor`
3. Spans exported to OTLP HTTP endpoint (`/v1/traces`)
4. Jaeger stores and indexes spans by trial ID

### Reading Path

1. `JaegerTraceReader` queries Jaeger HTTP API
2. Filters by service name and `dojozero.trial.id` tag
3. Returns `SpanData` list sorted by start time
4. Frontend processes spans to derive actor list and conversations

### Checkpoint Conversion

For replaying historical data, `load_spans_from_checkpoint()` converts checkpoint `actor_states` to spans:

1. Generate registration spans from actor metadata
2. Convert DataStream events to event spans
3. Convert Agent conversation history to message spans
4. Return sorted list (registration spans first, then events by time)

## API Integration

`GET /api/traces/{trial_id}` returns all spans for a trial. The arena UI derives all needed state (actors, conversations, timeline) from spans alone—no separate `agent_states` endpoint needed.

Query parameters:
- `since`: ISO timestamp to filter spans after a given time

## Trial Phase Determination

Frontend determines trial phase from lifecycle spans:

1. Check for `trial.started` and `trial.stopped` spans
2. Compare timestamps if both exist
3. Phase is "running" if started > stopped, "stopped" if stopped >= started
4. Phase is "unknown" if no lifecycle spans found
