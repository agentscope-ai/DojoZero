# Tracing

DojoZero uses OpenTelemetry to give you visibility into what happens during a trial — every event processed, every agent message, and every decision made. Without tracing, you only see terminal output; with it, you get a structured, searchable record of the entire trial.

**Install note:** **Jaeger** works with the **default** package install. **Alibaba Cloud Log Service (SLS)** as a trace backend requires optional dependencies: `pip install 'dojozero[alicloud]'`.

## 1. Backend: Jaeger

Install and start Jaeger: [https://www.jaegertracing.io/](https://www.jaegertracing.io/)

Run Dashboard Server with Jaeger:
```bash
# With default endpoints (localhost:4318)
dojo0 serve --trace-backend jaeger

# With custom endpoint
dojo0 serve --trace-backend jaeger --trace-ingest-endpoint http://localhost:4318
```

- UI: http://localhost:16686
- OTLP HTTP: http://localhost:4318
- Docs: https://www.jaegertracing.io/docs/

## 2. Backend: Alibaba Cloud SLS (Optional)

Use `--trace-backend sls` only after installing **`dojozero[alicloud]`**. Configure SLS project/endpoint/logstore via `DOJOZERO_SLS_*` (see `.env.example`). For querying traces in Arena/dashboard, the same extra is required.

## 3. Run Trials with Tracing

### Option 1: Via Dashboard Server (Recommended)
```bash
# Terminal 1: Start Dashboard Server
dojo0 serve --trace-backend jaeger

# Terminal 2: Run trial
dojo0 run \
  --params trial_params/nba-moneyline.yaml \
  --trial-id nba-server-001 \
  --server http://localhost:8000
```

Or, auto-schedule trials from trial sources:

```bash
dojo0 serve --trace-backend jaeger --trial-source "trial_sources/daily/*.yaml"
```

This registers the discovery sources so upcoming games trigger trials automatically, and the resulting trial runs are exported to Jaeger.


### Option 2: Standalone Usage
```bash
dojo0 run --params trial_params/nba-moneyline.yaml --trace-backend jaeger
```

## 4. What's in the traces?

DojoZero exports tracing data as a unified stream of spans. In Jaeger (and in Arena’s trace view), you’ll typically see three kinds of spans:

- **Resource spans (`*.registered`)**: one span per actor (agents and datastreams) carrying actor metadata. These include tags like `actor.id`, `actor.type`, and `dojozero.trial.id`.
- **Event spans**: one span per runtime `DataEvent` produced by the DataHub. Each event span uses the event type as its operation name (for example `game_start`, `odds_update`, `nfl_play`) and includes tags like `sequence`, `sport.type`, `game.id`, `game.date`, plus `event.*` tags for the event payload fields.
- **Agent message spans (`agent.*`)**: one span per agent conversation/message for the agent’s streams. Operation names include `agent.input` (user/system input), `agent.response` (assistant output), and `agent.tool_result` (system/tool results). These spans include tags such as `event.stream_id`, `event.role`, `event.name`, `event.content`, and (when present) `event.tool_calls` / `event.tool_call_id`.

You’ll also see **trial lifecycle spans** like `trial.started` / `trial.stopped`, and (for the built-in betting broker) **broker spans** such as `broker.bet` or `broker.<change_type>` with `broker.*` tags describing what happened (amount, selection, probabilities, etc.).
