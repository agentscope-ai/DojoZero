# Data Design

Unified span data format for trace storage and transmission, compatible with OpenTelemetry.

## Design Principle

- **Resource Spans** (`*.registered`): Actor metadata, emitted once per actor
- **Event Spans**: Runtime events with business data (`event.*` tags)

## Data Structure

```python
@dataclass(slots=True)
class SpanData:
    trace_id: str                    # Trial ID
    span_id: str                     # Unique span identifier
    operation_name: str              # Event type or operation name
    start_time: int                  # Microseconds since epoch
    duration: int                    # Microseconds (0 for instant events)
    parent_span_id: str | None       # Parent span for hierarchy
    tags: dict[str, Any]             # Key-value metadata
    logs: list[dict[str, Any]]       # Span logs/events
```

## JSON Wire Format

```json
{
  "traceID": "nba-trial-123",
  "spanID": "a1b2c3d4e5f6",
  "operationName": "game_update",
  "startTime": 1736251800000000,
  "duration": 0,
  "parentSpanID": null,
  "tags": [
    {"key": "dojozero.trial.id", "value": "nba-trial-123"},
    {"key": "dojozero.actor.id", "value": "nba_stream"},
    {"key": "dojozero.event.type", "value": "game_update"},
    {"key": "event.home_team", "value": "{\"score\": 85}"}
  ],
  "logs": []
}
```

## Standard Tags

### Framework Tags (`dojozero.*`)

| Tag | Description |
|-----|-------------|
| `dojozero.trial.id` | Trial identifier |
| `dojozero.actor.id` | Actor unique identifier |
| `dojozero.actor.type` | Actor type: "agent" or "datastream" |
| `dojozero.event.type` | Event type (same as operationName) |
| `dojozero.event.sequence` | Event sequence number |

### Resource Tags (`resource.*`) - Only in Registration Spans

| Tag | Description |
|-----|-------------|
| `resource.name` | Display name for the actor |
| `resource.model` | Model identifier (for agents) |
| `resource.model_provider` | Model provider: openai, anthropic, etc. |
| `resource.system_prompt` | System prompt (for agents) |
| `resource.tools` | Available tools JSON array (for agents) |
| `resource.source_type` | Source type (for datastreams) |

### Event Tags (`event.*`) - Business Data

| Tag | Description |
|-----|-------------|
| `event.stream_id` | Source stream ID |
| `event.role` | Agent message role (user/assistant/system) |
| `event.name` | Agent or tool name |
| `event.content` | Message content |
| `event.tool_calls` | Tool calls JSON array |
| `event.tool_call_id` | Tool call ID (for tool results) |
| `event.message_id` | Message unique ID |
| `event.*` | Other domain-specific data |

## Span Types

### 1. Registration Spans (Resource Spans)

Emitted once per actor when loaded from checkpoint. Contains actor metadata.

**Agent Registration:**
```json
{
  "operationName": "agent.registered",
  "tags": [
    {"key": "dojozero.trial.id", "value": "trial-123"},
    {"key": "dojozero.actor.id", "value": "betting_agent"},
    {"key": "dojozero.actor.type", "value": "agent"},
    {"key": "resource.name", "value": "BettingAgent"},
    {"key": "resource.model", "value": "gpt-4-turbo"},
    {"key": "resource.model_provider", "value": "openai"},
    {"key": "resource.system_prompt", "value": "You are a sports betting analyst..."},
    {"key": "resource.tools", "value": "[\"place_bet\", \"get_odds\"]"}
  ]
}
```

**DataStream Registration:**
```json
{
  "operationName": "datastream.registered",
  "tags": [
    {"key": "dojozero.trial.id", "value": "trial-123"},
    {"key": "dojozero.actor.id", "value": "nba_stream"},
    {"key": "dojozero.actor.type", "value": "datastream"},
    {"key": "resource.name", "value": "NBA Live Feed"},
    {"key": "resource.source_type", "value": "websocket"}
  ]
}
```

### 2. DataStream Event Spans

```json
{
  "operationName": "game_update",
  "tags": [
    {"key": "dojozero.trial.id", "value": "trial-123"},
    {"key": "dojozero.actor.id", "value": "nba_stream"},
    {"key": "dojozero.event.type", "value": "game_update"},
    {"key": "dojozero.event.sequence", "value": 42},
    {"key": "event.home_team", "value": "{\"score\":85,\"teamTricode\":\"LAL\"}"},
    {"key": "event.away_team", "value": "{\"score\":78,\"teamTricode\":\"BOS\"}"}
  ]
}
```

### 3. Agent Message Spans

```json
{
  "operationName": "agent.response",
  "tags": [
    {"key": "dojozero.trial.id", "value": "trial-123"},
    {"key": "dojozero.actor.id", "value": "betting_agent"},
    {"key": "dojozero.event.type", "value": "agent.response"},
    {"key": "dojozero.event.sequence", "value": 5},
    {"key": "event.stream_id", "value": "nba_stream"},
    {"key": "event.role", "value": "assistant"},
    {"key": "event.name", "value": "BettingAgent"},
    {"key": "event.content", "value": "Based on the current score..."},
    {"key": "event.tool_calls", "value": "[{\"name\":\"place_bet\",\"args\":{}}]"}
  ]
}
```

Agent operation names:
- `agent.input` (role: user)
- `agent.response` (role: assistant)
- `agent.tool_result` (role: system)

## Checkpoint Conversion

Spans are generated from checkpoint `actor_states`:

```python
def load_spans_from_checkpoint(
    trial_id: str,
    actor_states: dict[str, Any],
    since_us: int = 0,
) -> list[SpanData]:
    """Load all data from checkpoint and convert to spans.
    
    Returns registration spans first, then event spans sorted by time.
    """
    ...
```

## API Response

`GET /api/traces/{trial_id}` returns only spans (no separate agent_states):

```json
{
  "trial_id": "nba-trial",
  "spans": [
    {"operationName": "agent.registered", "tags": [...]},
    {"operationName": "datastream.registered", "tags": [...]},
    {"operationName": "game_update", "tags": [...]},
    {"operationName": "agent.input", "tags": [...]},
    {"operationName": "agent.response", "tags": [...]}
  ]
}
```

## Frontend Processing

The frontend processes spans to derive all needed state:

```javascript
function processSpans(rawSpans) {
    ...
}

function extractActors(spans) {
    ...
}

function groupConversations(spans) {
    ...
}
```

## Future: OpenTelemetry Integration

SpanData is designed for easy migration to OpenTelemetry:

```python
from opentelemetry.sdk.trace import SpanProcessor

class DashboardSpanProcessor(SpanProcessor):
    def on_end(self, span: ReadableSpan) -> None:
        span_data = SpanData(
            trace_id=format(span.context.trace_id, "032x"),
            span_id=format(span.context.span_id, "016x"),
            operation_name=span.name,
            start_time=span.start_time // 1000,  # ns to us
            duration=(span.end_time - span.start_time) // 1000,
            parent_span_id=format(span.parent.span_id, "016x") if span.parent else None,
            tags={k: v for k, v in span.attributes.items()},
        )
        await dashboard_store.add_span(trial_id, span_data)
```
