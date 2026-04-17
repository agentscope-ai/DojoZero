# Include LLM Chat Context in OTel Output

**Issue:** [#53](https://github.com/agentscope-ai/DojoZero/issues/53) — `[agent] include model context (e.g., chat messages for user and assistants) in OTel output`
**Status:** Draft
**Date:** 2026-04-17

## 1. Problem

Today, each agent turn emits two spans on the OTel pipeline:

- `agent.input` (`packages/dojozero/src/dojozero/betting/_agent.py:753`) — tags the consolidated event payload the agent will see.
- `agent.response` (`packages/dojozero/src/dojozero/betting/_agent.py:777`) — tags the final assistant text, CoT steps derived from memory diff, and any bet tool call.

Both are *agent-level* views. The actual LLM exchange is opaque:

- The **system prompt** is never exported (it is held inside `ReActAgent`, `betting/_agent.py:321`).
- The **full message history** (prior user/assistant/tool turns that actually went into the prompt) is not captured — only this-turn new messages reconstructed from an `InMemoryMemory` diff (`betting/_agent.py:938-951`).
- **Model-level metadata** is missing: model name, temperature / sampling params, token usage, stop/finish reason, tool schemas sent, raw assistant output (including reasoning blocks, refusals, etc.).
- A single `_react_agent(msg)` call (`betting/_agent.py:943`) can trigger **multiple LLM calls** (ReAct multi-step). We currently collapse all of them into one `agent.response` span, so per-call costs/latency/failures are invisible.
- No alignment with the OTel **GenAI semantic conventions** (no `gen_ai.*` attributes). Backends like Jaeger/Arena and third-party LLM observability tools can't recognize our spans as LLM calls.

This blocks debugging ("what did the model actually see?"), evaluation/replay, cost attribution, and use of standard GenAI tracing UIs.

## 2. Goals

1. Emit one span per **LLM call** containing the full input/output context, as a child of the current agent turn.
2. Capture: model id, request params, all messages (system/user/assistant/tool) with roles and content, tool schemas, finish reason, token usage, latency, error.
3. Conform to the OpenTelemetry **GenAI semantic conventions** ([`gen_ai.*`](https://opentelemetry.io/docs/specs/semconv/gen-ai/)) so traces are portable.
4. Make content capture **opt-out configurable** (size caps, on/off) for PII / backend-size concerns.
5. Keep existing `agent.input` / `agent.response` spans unchanged — this is additive.

## 3. Non-goals

- Automatic instrumentation of every AgentScope component. We only instrument the LLM boundary.
- Replacing the `agent.response` CoT-step synthesis — that remains the high-level, UI-friendly view.
- Real-time PII scrubbing. Users opt out of content capture when needed.
- Cross-provider token normalization. We record whatever the provider returns.

## 4. Current state (references)

| Concern | Location |
|---|---|
| OTel exporter, `SpanData`, `emit_span` | `packages/dojozero/src/dojozero/core/_tracing.py:62`, `:1369`, `:1804`, `:2363` |
| Dashboard-server exporter init, flags | `packages/dojozero/src/dojozero/dashboard_server/_server.py` (`--trace-backend`, `--trace-ingest-endpoint`) |
| Agent LLM call site | `packages/dojozero/src/dojozero/betting/_agent.py:943` (`await self._react_agent(msg)`) |
| Existing agent spans | `betting/_agent.py:753` (`agent.input`), `:777` (`agent.response`) |
| Memory diff → CoT | `betting/_agent.py:123`, `:133` |
| AgentScope model abstraction | `ReActAgent(model=ChatModelBase, formatter=..., toolkit=..., memory=...)` at `betting/_agent.py:321` |
| OTel deps | `packages/dojozero/pyproject.toml` — `opentelemetry-api/sdk/exporter-otlp-proto-http >= 1.29.0` |
| Tracing docs | `docs/tracing.md` |

## 5. Design

### 5.1 Instrumentation point

Wrap AgentScope's `ChatModelBase` with a tracing decorator rather than parsing memory diffs. Memory diffs miss the system prompt, tool schemas, and per-call metadata, and they lose the 1:N mapping between an agent turn and LLM calls.

```
ReActAgent(model = TracingChatModel(inner=<provider model>, trial_id=..., actor_id=...))
```

`TracingChatModel` forwards `__call__` (and streaming variants) to the wrapped model, timing the call and emitting one span per invocation with full request/response. This keeps us provider-agnostic: OpenAI / Dashscope / Anthropic / Gemini all go through `ChatModelBase` in AgentScope.

A thin factory in `core/_tracing.py` (e.g. `wrap_model_for_tracing(model, trial_id, actor_id)`) is called from `BettingAgent.__init__` right before passing the model to `ReActAgent`.

### 5.2 Span shape (GenAI semconv)

- **Name:** `chat {model}` (per semconv), e.g. `chat qwen-max`. Fallback `chat` if model id unknown.
- **Kind:** `CLIENT`.
- **Parent:** the current `agent.response` span (we'll open `agent.response` as a real span around the `_react_agent` call and close it after, instead of emitting it post-hoc — see §5.4).

Attributes (set when available):

| Attribute | Source |
|---|---|
| `gen_ai.system` | Derived from model class (`openai`, `dashscope`, `anthropic`, `gemini`) |
| `gen_ai.operation.name` | `"chat"` |
| `gen_ai.request.model` | Request model id |
| `gen_ai.response.model` | Response model id (if different) |
| `gen_ai.request.temperature`, `.top_p`, `.max_tokens`, `.frequency_penalty`, `.presence_penalty`, `.stop_sequences` | Request kwargs |
| `gen_ai.response.id` | Provider response id |
| `gen_ai.response.finish_reasons` | List of finish reasons |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | Usage block |
| `dojozero.trial.id`, `dojozero.actor.id`, `dojozero.turn.sequence` | Dojozero context |

Content (messages + tool schemas) goes on the span as **events** per semconv, one event per message:

- `gen_ai.system.message` — `{ "content": ... }`
- `gen_ai.user.message` — `{ "content": ..., "name"?: ... }`
- `gen_ai.assistant.message` — `{ "content": ..., "tool_calls"?: [...] }`
- `gen_ai.tool.message` — `{ "content": ..., "id": ... }`
- `gen_ai.choice` (one per returned choice) — `{ "index", "finish_reason", "message": {...} }`

Tool schema list is attached as `gen_ai.request.tools` (JSON string) on the span itself, because it is per-request not per-message.

On error we set span status `ERROR` and record the exception via `record_exception`.

### 5.3 Backend compatibility fallback

Our OTel export path (`SpanData` in `core/_tracing.py:62`) today carries `tags` and `logs`. The generic Jaeger-OTLP path supports span events natively; the SLS `SLSLogExporter` path will need to flatten events into log lines on the span — this is already how `logs` works in `SpanData`, so mapping events → `SpanData.logs` is a small extension (one log entry per message, with `event.name` set to the semconv event name).

For backends that render attributes better than events, also write a compact JSON string attribute `gen_ai.prompt` (list of `{role, content}`) and `gen_ai.completion` (list of choices). These are redundant with events but cheap and widely supported.

### 5.4 Relationship to existing spans

- `agent.input` — unchanged; still emitted per event push.
- `agent.response` — promoted from post-hoc `emit_span` to a **real context-managed span** opened before `await self._react_agent(msg)` and closed after. This gives every `chat …` span a sensible parent. Tags and message payload are unchanged.
- `chat {model}` — new, 1…N per `agent.response`, emitted by `TracingChatModel`.

```
agent.input ─┐
             └─ agent.response ─┬─ chat qwen-max   (turn step 1: reason + tool_use)
                                ├─ chat qwen-max   (turn step 2: after tool_result)
                                └─ …
```

### 5.5 Configuration

New env vars (standard `DOJOZERO_` prefix, plumbed via existing Pydantic settings):

| Var | Default | Meaning |
|---|---|---|
| `DOJOZERO_TRACE_GENAI` | `true` | Master switch for `chat` spans |
| `DOJOZERO_TRACE_GENAI_CONTENT` | `true` | If false, emit span + metadata but **omit** message/tool content |
| `DOJOZERO_TRACE_GENAI_CONTENT_MAX_CHARS` | `32768` | Per-message content truncation cap; truncated events get a `gen_ai.truncated=true` attribute and original length |
| `DOJOZERO_TRACE_GENAI_INCLUDE_TOOLS` | `true` | Whether to attach `gen_ai.request.tools` |

Also honor the upstream OTel standard when present: `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` (`true`/`false`) overrides `DOJOZERO_TRACE_GENAI_CONTENT` if set.

Document these in `docs/tracing.md` §4 alongside the existing span taxonomy.

## 6. Implementation plan

1. **Add `TracingChatModel`** in `core/_tracing.py` (or a new `core/_genai_tracing.py`) — a generic wrapper that conforms to AgentScope's `ChatModelBase` protocol, forwarding all methods and emitting spans. Start with non-streaming; add streaming (`__call__` yielding chunks) in a follow-up, aggregating chunks into the final message for the span.
2. **Extend `SpanData`** with an optional `events: list[SpanEvent]` field (name, timestamp, attrs). Map to OTLP span events in `OTelSpanExporter` and to `logs` in `SLSLogExporter`.
3. **Promote `agent.response` to a context-managed span** in `BettingAgent._process_events` (`betting/_agent.py:877`). Keep all existing tags. This only changes lifecycle, not payload.
4. **Wire the wrapper** in `BettingAgent.__init__` (`betting/_agent.py:321`) — wrap `model` before constructing `ReActAgent`.
5. **Settings**: add the env vars to the existing settings model; read them inside `TracingChatModel`.
6. **Docs**: update `docs/tracing.md` with the new `chat …` span type and the content-capture flags.
7. **Tests**:
   - Unit: `TracingChatModel` emits a span with the expected GenAI attributes/events for a mocked model; respects content flag; truncates long content; records exceptions on failure.
   - Integration (marked `@pytest.mark.integration`): run a short trial against a stub model and assert that for each agent turn we see `agent.response` with ≥1 `chat` child carrying `gen_ai.usage.*`.

## 7. Open questions

1. **Streaming:** AgentScope streaming yields chunks; do we open the span at first chunk and close at end-of-stream, or at call return? Proposal: open at call, record token events as `gen_ai.choice.delta` events (optional), close at end.
2. **Tool-call fan-out inside one `chat`:** OpenAI returns multiple tool calls in one assistant message. Semconv represents this in the `gen_ai.choice` event; no extra spans per tool call. Matches our §5.4 model.
3. **Message content size in SLS:** SLS log-field size limits may force per-message chunking or external storage with a pointer. Decide after measuring real trials. For now, rely on the `MAX_CHARS` truncation.
4. **Redaction of system prompt:** some users may consider the system prompt proprietary. Current proposal: governed by the same `DOJOZERO_TRACE_GENAI_CONTENT` flag. Do we need a separate `_SYSTEM` flag?
5. **Back-compat for Arena UI:** Arena renders `agent.response` today. Adding `chat` children should be a no-op for Arena, but we should verify the trace view doesn't break with a span that has no Dojozero-specific tags.
6. **Naming of the wrapped-model factory:** `wrap_model_for_tracing` vs a decorator `@traced_chat_model`. Either works; propose the factory for explicitness.

## 8. Rollout

- Phase 1 (this issue): `TracingChatModel`, span-event extension, wiring in `BettingAgent`, docs, tests. Flag on by default.
- Phase 2: streaming support, SLS-side flattening review, optional upload of large prompts to object storage with a `gen_ai.prompt.ref` pointer.
- Phase 3: apply to any non-betting agents (sample agents, future `AgentGroup`) once the shape is stable.
