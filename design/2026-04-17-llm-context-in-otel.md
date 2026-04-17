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

### 5.5 SLS field-size constraints

The SLS exporter (`SLSLogExporter` in `core/_tracing.py`, ~lines 2033-2334) flattens span tags/logs into one log entry, with all values stringified. There is **no truncation today**. Relevant SLS service limits:

| Limit | Value | Source |
|---|---|---|
| `PutLogs` request total | 10 MB | [Aliyun docs — Limits](https://www.alibabacloud.com/help/en/sls/product-overview/limits-1) |
| Single log-record value (per field) | ~1 MB (service-side; not enforced by SDK) | Aliyun docs (referenced) |

Implications:

- A single LLM call can easily produce >100 KB of message content (long event-history user messages + tool results). One conservative serialized blob per message (`gen_ai.user.message` etc.) is fine well under 1 MB, but a multi-turn agent over a long trial could push a single span past the per-record budget if all messages are repeated on every call.
- We don't need offloading in phase 1, but we do need:
  - A per-message truncation cap (default 256 KB chars; cheap to bump if needed).
  - A per-span hard cap (default 4 MB total content) — if exceeded, drop oldest non-system messages first and set `gen_ai.truncated=true` with `gen_ai.truncated.dropped_messages=N`.
  - Truncated content events get `gen_ai.truncated=true` and `gen_ai.original_length=<chars>`.

Object-storage offloading (`gen_ai.prompt.ref` pointing at OSS) is deferred to phase 2 and only triggered if real trials exceed the per-span cap regularly.

### 5.6 Configuration

New env vars (standard `DOJOZERO_` prefix, plumbed via existing Pydantic settings):

| Var | Default | Meaning |
|---|---|---|
| `DOJOZERO_TRACE_GENAI` | `true` | Master switch for `chat` spans |
| `DOJOZERO_TRACE_GENAI_CONTENT` | `true` | If false, emit span + metadata but **omit** message/tool content |
| `DOJOZERO_TRACE_GENAI_CONTENT_MAX_CHARS` | `262144` (256 KB) | Per-message content truncation cap (chosen against SLS ~1 MB/field limit) |
| `DOJOZERO_TRACE_GENAI_SPAN_MAX_CHARS` | `4194304` (4 MB) | Per-span total content cap; oldest non-system messages dropped beyond this |
| `DOJOZERO_TRACE_GENAI_INCLUDE_TOOLS` | `true` | Whether to attach `gen_ai.request.tools` |

Also honor the upstream OTel standard when present: `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` (`true`/`false`) overrides `DOJOZERO_TRACE_GENAI_CONTENT` if set.

Document these in `docs/tracing.md` §4 alongside the existing span taxonomy.

## 6. Implementation plan

1. **Add `TracingChatModel`** in `core/_tracing.py` (or a new `core/_genai_tracing.py`) — a generic wrapper that conforms to AgentScope's `ChatModelBase` protocol, forwarding all methods and emitting spans. Constructed via a `wrap_model_for_tracing(model, trial_id, actor_id)` factory. **Non-streaming only.** Streaming is intentionally out of scope; eliminating any remaining streaming use is tracked as a separate issue.
2. **Extend `SpanData`** with an optional `events: list[SpanEvent]` field (name, timestamp, attrs). Map to OTLP span events in `OTelSpanExporter` and to `logs` in `SLSLogExporter`.
3. **Promote `agent.response` to a context-managed span** in `BettingAgent._process_events` (`betting/_agent.py:877`). Keep all existing tags. This only changes lifecycle, not payload.
4. **Wire the wrapper** in `BettingAgent.__init__` (`betting/_agent.py:321`) — wrap `model` before constructing `ReActAgent`.
5. **Settings**: add the env vars to the existing settings model; read them inside `TracingChatModel`.
6. **Docs**: update `docs/tracing.md` with the new `chat …` span type and the content-capture flags.
7. **Tests**:
   - Unit: `TracingChatModel` emits a span with the expected GenAI attributes/events for a mocked model; respects content flag; truncates long content; records exceptions on failure.
   - Integration (marked `@pytest.mark.integration`): run a short trial against a stub model and assert that for each agent turn we see `agent.response` with ≥1 `chat` child carrying `gen_ai.usage.*`.

## 7. Resolved decisions

- **Streaming**: out of scope. Eliminating remaining streaming use is its own issue.
- **Tool-call fan-out inside one `chat`**: no extra spans per tool call; modeled via the assistant message's `tool_calls` and `gen_ai.choice` event per semconv.
- **System-prompt redaction**: not needed — all traced agents are built-in. Single `DOJOZERO_TRACE_GENAI_CONTENT` flag governs all content.
- **Arena UI**: do **not** add a trace view for `chat` spans in this phase. New spans are background-only; Arena keeps rendering `agent.response` as before.
- **Factory naming**: use `wrap_model_for_tracing(model, trial_id, actor_id)`.

## 8. Rollout

- Phase 1 (this issue): `TracingChatModel`, span-event extension, wiring in `BettingAgent`, docs, tests. Flag on by default.
- Phase 2: object-storage offload (`gen_ai.prompt.ref`) if SLS per-span budget proves tight in real trials.
- Phase 3: apply to any non-betting agents (sample agents, future `AgentGroup`) once the shape is stable.
