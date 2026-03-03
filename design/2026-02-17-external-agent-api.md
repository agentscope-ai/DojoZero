# External Agent API Design

**Date**: 2026-02-17
**Status**: Draft

---

## Executive Summary

Enable third-party agents to participate in DojoZero trials via HTTP APIs (REST + SSE). Trials remain isolated units with per-trial DataHub; agents can subscribe to multiple trials simultaneously.

**Key decisions:**
- Trial-level containerization
- Gateway embedded in trial (process or container)
- Per-trial DataHub (no centralized event bus)
- Agents manage their own multi-trial state

---

## 1. Goals

| Goal | Priority |
|------|----------|
| Third-party agents in any language | High |
| Low latency for betting | High |
| Backwards compatibility with internal agents | High |
| Clean trial lifecycle management | Medium |

**Non-goals (this phase):** Per-agent containerization, event mesh (NATS/Kafka), multi-region.

---

## 2. Architecture

### 2.1 Per-Trial Gateway

Each trial runs its own Gateway, whether as a process or container. Co-locating Gateway with DataHub minimizes latency (no extra network hop for events or bets).

```mermaid
graph TB
    subgraph Trial["Trial (Process or Container)"]
        TO["TrialOrchestrator + DataHub + Agents"]
        TO --> GW
        subgraph GW["Agent Gateway (:8080)"]
            REST["REST API"]
            SSE["SSE Stream"]
            SUB["Subscription Mgr"]
        end
    end
    EA1["External Agent 1"] --> GW
    EA2["External Agent 2"] --> GW
```

### 2.2 Deployment Modes

Two gateway topologies:

**A) Per-Trial Gateway** (standalone trials)
```mermaid
graph LR
    Agent --> TA["Trial A Gateway (:8080)"]
    Agent --> TB["Trial B Gateway (:8081)"]
```

**B) Routing Proxy** (Dashboard Server)
```mermaid
graph LR
    Agent --> DS["Dashboard Server (:8000)"]
    DS --> TA["Trial A"]
    DS --> TB["Trial B"]
    DS --> TC["Trial C"]
```

| Mode | Topology | How to Run |
|------|----------|------------|
| **Local dev** | Per-trial | `dojo0 run --enable-gateway --gateway-port 8080` |
| **Dashboard Server** | Routing proxy | `dojo0 serve --enable-gateway` |
| **Container (prod)** | Per-trial + reverse proxy | K8s Ingress routes to containers |

**Local dev:**
```bash
dojo0 run --params trial.yaml --enable-gateway
# Agent connects to localhost:8080/api/v1/...
```

**Dashboard Server:**
```bash
dojo0 serve --enable-gateway --trace-backend jaeger
# Agent connects to localhost:8000/api/gateway/{trial_id}/...
# Dashboard routes to correct trial internally
```

Both topologies use the same API - only the URL prefix differs.

### 2.3 Scaling and Isolation

#### Primary Model: In-Process Trials with Sharded Dashboards

Trials run in-process within Dashboard Server. For scaling and isolation, deploy multiple Dashboard instances (shards).

```mermaid
graph TB
    subgraph External
        EA["External Agents"]
    end

    LB["Load Balancer"]

    subgraph Cluster["Dashboard Shards"]
        subgraph DA["Dashboard A"]
            T1["Trial 1"]
            T2["Trial 2"]
            T3["Trial 3"]
        end

        subgraph DB["Dashboard B"]
            T4["Trial 4"]
            T5["Trial 5"]
            T6["Trial 6"]
        end

        subgraph DC["Dashboard C"]
            T7["Trial 7"]
            T8["Trial 8"]
            T9["Trial 9"]
        end
    end

    REG["Registry<br/>trial → shard"]

    EA --> LB
    LB --> DA
    LB --> DB
    LB --> DC
    DA -.-> REG
    DB -.-> REG
    DC -.-> REG
```

#### Why In-Process Over Containers?

| Concern | In-Process | Containers |
|---------|------------|------------|
| SSE streaming | ✅ Zero latency (direct) | ⚠️ Proxy complexity |
| Trial startup | ✅ ~10ms | ❌ 2-5 seconds |
| Lifecycle management | ✅ Simple (async tasks) | ⚠️ Docker/K8s API |
| Debugging | ✅ Single process | ⚠️ Distributed logs |
| Operational complexity | ✅ Low | ⚠️ Higher |
| Crash isolation | ⚠️ Per shard | ✅ Per trial |
| Dependency isolation | ❌ Shared env | ✅ Per container |

**In-process wins** because:
- All trials share the same DojoZero codebase (no dependency conflicts)
- External agents connect via HTTP API (no code execution in trials)
- SSE streaming is trivial (direct function calls)
- Sharding provides sufficient isolation

#### Isolation via Sharding

```
Dashboard A crashes
├── Trial 1 ── affected
├── Trial 2 ── affected
└── Trial 3 ── affected

Dashboard B (healthy)
├── Trial 4 ── ✅ unaffected
├── Trial 5 ── ✅ unaffected
└── Trial 6 ── ✅ unaffected
```

**Blast radius:** 1/N of trials (where N = number of shards).

#### Fault Tolerance

| Mechanism | Status | Description |
|-----------|--------|-------------|
| Checkpointing | ✅ Exists | Periodic save of actor state |
| Event persistence | ✅ Exists | DataHub writes to JSONL |
| Auto-resume | ✅ Exists | Dashboard resumes trials on restart |
| Client reconnection | ✅ In SDK | `Last-Event-ID` for SSE resume |

**Recovery flow:**
1. Dashboard crashes
2. Load balancer detects, routes to healthy shards
3. Dashboard restarts, scans for interrupted trials
4. Resumes from latest checkpoint
5. External agents reconnect via SDK

#### Capacity Planning

| Resource | Per Trial | 32GB/8-core node |
|----------|-----------|------------------|
| Memory | 200-500MB | ~50-100 trials |
| CPU (external LLM) | 0.1-0.2 cores | ~40-80 trials |
| **Practical limit** | | **20-50 trials per Dashboard** |

**Scaling path:**
```
1 Dashboard  →  20-50 trials
3 Dashboards →  60-150 trials
N Dashboards →  N × 50 trials
```

#### Third-Party Agent Security

External agents connect via HTTP API—they do NOT execute code inside trials.

```
┌─────────────────────────────────────┐
│  External Agent (their infra)       │
│  - Their code, their servers        │
└─────────────────────────────────────┘
                │
                │ HTTP API (trust boundary)
                ▼
┌─────────────────────────────────────┐
│  Trial (your infra)                 │
│  - Validates requests               │
│  - Rate limits                      │
│  - Enforces business rules          │
└─────────────────────────────────────┘
```

**Security is at the API layer**, not the process boundary. Containers don't add security for this threat model.

| Threat | Mitigation | Container Helps? |
|--------|------------|------------------|
| API spam | Rate limiting | ❌ No |
| Unauthorized access | JWT auth | ❌ No |
| Data leakage | Access control | ❌ No |
| Bet manipulation | Validation | ❌ No |

#### When to Consider Containers

Containers become necessary if:

| Scenario | Why Containers |
|----------|----------------|
| Different dependencies per trial | Isolated Python environments |
| Agents submit code to execute | Sandboxing required |
| Per-trial resource metering | Container-level metrics |
| Compliance/audit requirements | Stronger isolation boundary |
| Zero-downtime trial deployments | Rolling updates per trial |

**Current DojoZero:** None of these apply. In-process with sharding is sufficient.

#### Alternative: Ray Runtime

DojoZero supports Ray for distributed execution (`--runtime-provider ray`).

| Aspect | Ray | In-Process |
|--------|-----|------------|
| Multi-host | ✅ Yes | ❌ No (use sharding) |
| Process isolation | ✅ Yes | ❌ Shared process |
| SSE streaming | ⚠️ Requires polling (~50-100ms latency) | ✅ Zero latency |
| Complexity | Medium | Low |

**Ray limitation:** Ray actors don't support native streaming. Dashboard must poll DataHub actor for events, adding latency.

**Recommendation:** Use in-process for external agent trials (zero SSE latency). Consider Ray for compute-heavy internal trials without external agent access.

#### Deployment Example

```yaml
# docker-compose.yml - Sharded Dashboards
services:
  dashboard-a:
    image: dojozero
    command: ["dojo0", "serve", "--enable-gateway", "--port", "8000"]
    ports:
      - "8001:8000"
    environment:
      - SHARD_ID=a

  dashboard-b:
    image: dojozero
    command: ["dojo0", "serve", "--enable-gateway", "--port", "8000"]
    ports:
      - "8002:8000"
    environment:
      - SHARD_ID=b

  nginx:
    image: nginx
    ports:
      - "8000:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - dashboard-a
      - dashboard-b
```

Each Dashboard runs trials in-process. Nginx routes by trial ID or round-robin.

### 2.4 Subscription Architecture

Shared `SubscriptionManager` with different transports:

```mermaid
graph TB
    subgraph DataHub
        SM["SubscriptionManager<br/>subscribe(filters) → Subscription<br/>backpressure, buffering, sequence tracking"]
    end
    SM -->|Direct call<br/>in-process| IA["Internal Agent A"]
    SM -->|Direct call<br/>in-process| IB["Internal Agent B"]
    SM -->|API Gateway<br/>HTTP/SSE| EC["External Agent C"]
```

**Internal agents:** Direct `subscription_manager.subscribe()` call - no serialization overhead.

**External agents:** Gateway wraps SubscriptionManager, adds HTTP transport (SSE/REST).

```python
# Internal agent - direct
class BettingAgent(Agent):
    async def start(self):
        sub = self.data_hub.subscribe(filters={"event_types": ["event.nba_*"]})
        async for event in sub:
            await self.on_event(event)

# External agent - via Gateway SSE
# Same subscription logic, different transport
```

### 2.5 Component Summary

| Component | Purpose | New/Existing |
|-----------|---------|--------------|
| SubscriptionManager | Shared subscription logic (filters, backpressure) | New |
| Agent Gateway | HTTP transport (SSE/REST) for external agents | New |
| ExternalAgentAdapter | Bridges API calls to Actor protocol | New |
| DataHub | Per-trial event bus | Existing |
| BrokerOperator | Bet execution | Existing |

---

## 3. Multi-Trial Agent Pattern

Agents can subscribe to multiple trials simultaneously. Each trial is independent; agents manage their own cross-trial state.

```mermaid
graph TB
    subgraph Agent["External Agent"]
        Router["Event Router<br/>(routes by trial_id/game_id)"]
        Router --> SA["Trial A State<br/>balance, bets"]
        Router --> SB["Trial B State<br/>balance, bets"]
        Router --> SC["Trial C State<br/>balance, bets"]
    end
    GA["Trial A Gateway"] -->|SSE| Router
    GB["Trial B Gateway"] -->|SSE| Router
    GC["Trial C Gateway"] -->|SSE| Router
```

**Agent responsibilities:**
- Maintain N connections (one per trial)
- Route events by `trial_id` or `game_id`
- Track state per trial
- Place bets via correct trial's API

### 3.1 Why Not an Aggregation Gateway?

Considered and rejected. An aggregation gateway would:
- Become a single point of failure
- Bottleneck all event traffic
- Not scale when trials live on different hosts
- Add latency (extra hop)

Direct connections scale better - same pattern as Kafka consumers, gRPC clients.

### 3.2 Client SDK Handles Connection Complexity

The DX concern (N connections is complex) is addressed by the client SDK, not server infrastructure:

```python
from dojozero_client import DojoClient

client = DojoClient(api_key="...")

# SDK manages connection pool internally
async with client.multi_subscribe(["trial-a", "trial-b", "trial-c"]) as stream:
    async for event in stream:
        # Events from all trials, tagged with trial_id
        print(f"[{event.trial_id}] {event.type}: {event.payload}")
```

**SDK handles internally:**
- Connection pooling (N SSE connections)
- Per-trial reconnection with `Last-Event-ID`
- Merged event stream with `trial_id` tagging
- Independent backpressure per connection
- Graceful degradation (one trial down ≠ all down)

**Why not centralized DataHub?**

| Concern | Per-Trial | Centralized |
|---------|-----------|-------------|
| Isolation | Natural | Needs ACLs |
| Failure blast radius | One trial | All trials |
| Checkpointing | Simple | Complex |
| Cross-game betting | Agent connects to multiple | Single connection |

Per-trial keeps things simple. Agents wanting a global view just subscribe to all trials.

---

## 4. Agent Gateway API

### 4.0 Versioning

- API versioned via URL path: `/api/v1/...`
- Breaking changes require new version (`v2`)
- Old versions supported for 6 months after deprecation notice
- Client SDK follows semver; major version = API version

### 4.1 REST Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/v1/register` | Register agent |
| DELETE | `/api/v1/register/{id}` | Unregister |
| GET | `/api/v1/trial` | Trial metadata |
| GET | `/api/v1/events/recent` | Recent events |
| GET | `/api/v1/odds/current` | Current odds |
| POST | `/api/v1/bets` | Submit bet |
| GET | `/api/v1/bets` | Agent's bets |
| GET | `/api/v1/balance` | Agent's balance |
| GET | `/api/v1/results` | Trial results (live or concluded) |

**Dashboard Server Endpoints** (routing proxy mode):

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/trials/{trial_id}/results` | Unified results endpoint |

The unified results endpoint works for both running and concluded trials:
- **Running trials:** Fetches live results from the gateway adapter
- **Concluded trials:** Returns persisted results from storage

### 4.2 Streaming Endpoints

| Endpoint | Protocol | Purpose |
|----------|----------|---------|
| `GET /api/v1/events/stream` | SSE | Real-time event push |
| `GET /api/v1/events?since=N` | REST | Polling fallback |

**Special SSE Events:**

| Event Type | Purpose |
|------------|---------|
| `event` | Game/data events (standard) |
| `heartbeat` | Connection keep-alive (every 30s) |
| `trial_ended` | Trial has concluded |

The `trial_ended` event is sent when a trial completes (game ends or manual stop):
```json
{
  "type": "trial_ended",
  "trialId": "lal-bos-2026-02-15",
  "reason": "completed",
  "message": "Game ended: LAL 110 - BOS 98",
  "finalResults": [
    {"agentId": "agent1", "finalBalance": "1200", "netProfit": "200", ...}
  ],
  "timestamp": "2026-02-15T22:45:00Z"
}
```

After receiving `trial_ended`, the SSE connection remains open briefly (grace period) to allow final result queries, then closes. Agents should:
1. Stop placing bets
2. Query final results if needed
3. Gracefully disconnect

### 4.3 Transport Auto-Detection

Client SDK auto-detects best transport:

```python
client = DojoClient(api_key="...", transport="auto")  # default
# or explicit: transport="sse" | "rest"
```

**Detection logic:**
1. Try SSE connection with 2s timeout
2. If successful → use SSE
3. If timeout/connection error → fall back to REST polling
4. On SSE disconnect mid-stream → auto-fallback to REST
5. Log transport selection for observability

```python
async def _detect_transport(self, trial_url: str) -> str:
    if self.transport != "auto":
        return self.transport
    try:
        async with timeout(2.0):
            await self._sse_probe(trial_url)
        logger.info(f"Using SSE for {trial_url}")
        return "sse"
    except (TimeoutError, ConnectionError):
        logger.info(f"SSE unavailable, using REST for {trial_url}")
        return "rest"
```

Developer can override auto-detection when needed.

### 4.4 Message Format

```json
{
  "type": "event",
  "trial_id": "lal-bos-2026-01-15",
  "sequence": 42,
  "timestamp": "2026-01-15T19:05:30Z",
  "payload": {
    "event_type": "event.nba_play",
    "game_id": "401584722",
    "description": "LeBron James makes 3-pointer"
  }
}
```

Events include `trial_id` for multi-trial agent routing.

---

## 5. Security

### 5.1 Authentication

JWT with RSA-256:
```
POST /auth/token (API key) → JWT (15 min expiry)
API calls with Bearer token → Gateway validates
```

### 5.2 Authorization

| Resource | Allowed |
|----------|---------|
| Trial events, odds | Read if registered |
| Own balance/bets | Read/write |
| Other agents' data | Never |

### 5.3 Rate Limiting

Default limits:

| Resource | Limit |
|----------|-------|
| Requests/min | 300 |
| SSE connections | 5 |
| Bets/min | 60 |

### 5.4 Error Handling

Standard error response format:
```json
{
  "error": {
    "code": "BET_REJECTED",
    "message": "Betting window closed",
    "details": {"event_sequence": 42, "current_sequence": 45}
  }
}
```

| HTTP Status | Meaning |
|-------------|---------|
| 400 | Bad request (malformed) |
| 401 | Auth required / token expired |
| 403 | Not authorized for this trial |
| 404 | Trial/resource not found |
| 409 | Conflict (e.g., duplicate bet with same idempotency key) |
| 429 | Rate limited |
| 503 | Trial unavailable |

**Retry guidance:** 429 and 503 include `Retry-After` header.

### 5.5 Outcome Gaming Prevention

1. Bets must reference recent event sequence (reject if sequence too stale)
2. `BettingOperator.can_bet` checks event status
3. Server-side timestamp validation only (no client timestamp - easily spoofed, penalizes high-latency agents)

---

## 6. Data Streaming

### 6.1 Subscription

```json
{
  "filters": {
    "event_types": ["event.nba_*", "event.odds_update"]
  },
  "options": {
    "include_snapshot": true
  }
}
```

### 6.2 Backpressure

Configurable per-subscription. Defaults:

| Buffer Depth | Action | Rationale |
|--------------|--------|-----------|
| < 100 | Normal | ~10 seconds of typical game events |
| 100-500 | Batch play events | Agent is slow but catching up |
| 500-1000 | Drop low-priority | Agent severely behind |
| > 1000 | Disconnect with resumption token | Prevent memory exhaustion |

**Priority:** Critical (lifecycle, odds) > High (game updates) > Normal (plays)

Agents can request custom thresholds at subscribe time if defaults don't fit their processing speed.

### 6.3 Reconnection

Gateway buffers last 100 events per subscription. On reconnect, replays from last sequence.

---

## 7. Critical Analysis

### 7.1 Do We Need This Now?

Current architecture already provides:
- Serializable trial specs (YAML)
- Checkpoint/resume
- Ray runtime for isolation

**Recommendation:** Start with API only. Containerization is a deployment concern, not architectural requirement.

### 7.2 Failure Modes

| Failure | Mitigation |
|---------|------------|
| SSE disconnect | Reconnect with `Last-Event-ID` |
| Bet timeout | Idempotency keys |
| Trial crash | Health checks, notifications |

---

## 8. Agent Integration Layers

External agents can interact with DojoZero at two levels:

```mermaid
graph TB
    subgraph Levels["Integration Layers"]
        L2["Level 2: dojozero-client SDK<br/>Handles auth, reconnection, transport"]
        L1["Level 1: Raw HTTP API<br/>Any language, full control"]
        L3["Level 3: DojoAgent base class<br/>(deferred)"]
    end
    L2 --> L1
    L3 -.->|future| L2
```

### 8.1 Level 1: Raw HTTP API

For any language or existing agent framework. Full control, no dependencies.

```bash
# Auth
curl -X POST https://dojo.api/auth/token \
  -d '{"api_key": "..."}' \
  -H "Content-Type: application/json"
# Returns: {"token": "eyJ..."}

# Subscribe to events (SSE)
curl -N https://dojo.api/trials/lal-bos-2026/events \
  -H "Authorization: Bearer eyJ..." \
  -H "Accept: text/event-stream"

# Place bet (REST)
curl -X POST https://dojo.api/trials/lal-bos-2026/bets \
  -H "Authorization: Bearer eyJ..." \
  -d '{"market": "moneyline", "selection": "home", "amount": 100}'
```

**Best for:** Existing agent systems (Moltenbook), non-Python agents, maximum flexibility.

### 8.2 Level 2: Python Client SDK

```bash
pip install dojozero-client  # Standalone package, minimal deps
```

Thin wrapper handling auth, transport selection, reconnection.

```python
from dojozero_client import DojoClient

client = DojoClient(api_key="...")

async def main():
    async with client.connect_trial("lal-bos-2026") as trial:
        # Subscribe - SDK picks SSE (local) or REST polling (remote)
        async for event in trial.events(filters=["event.nba_*"]):

            # Agent's own logic
            if should_bet(event):
                result = await trial.place_bet(
                    market="moneyline",
                    selection="home",
                    amount=100
                )
                print(f"Bet placed: {result.bet_id}")

        # Query state anytime
        balance = await trial.get_balance()
        odds = await trial.get_current_odds()
```

**SDK handles:**
- Auth token refresh
- Transport auto-detection (SSE preferred, REST fallback)
- Reconnection with `Last-Event-ID` / sequence replay
- Typed event models

**Best for:** Python agents, quick integration, don't want to handle HTTP details.

### 8.3 Level 3: DojoAgent Base Class (Deferred)

Opinionated framework with `on_event()` hooks. Deferred until API stabilizes. May be contributed by community.

### 8.4 CLI Tools

For debugging and quick testing without writing code.

**Trial Discovery (server CLI - already exists):**
```bash
# List available trials from Dashboard Server
dojo0 list-trials --server https://dojo.api

# With filters
dojo0 list-trials --server https://dojo.api --running-only
dojo0 list-trials --server https://dojo.api --scheduled-only
```

**Trial Interaction (client CLI - new):**
```bash
# Subscribe and print events
dojozero-agent subscribe --trial-url https://dojo.api/gateway/lal-bos-2026 --filter "event.nba_*"

# Check balance
dojozero-agent balance --trial-url https://dojo.api/gateway/lal-bos-2026

# Place a test bet
dojozero-agent bet --trial-url https://dojo.api/gateway/lal-bos-2026 --market moneyline --amount 100
```

**Why the split:**
- `dojo0 list-trials` queries the Dashboard Server (trial registry/discovery)
- `dojozero-agent` connects to individual trial gateways (interaction)
- The client SDK (`dojozero-client`) is lightweight and doesn't need Dashboard Server knowledge

**Best for:** Debugging, exploring API, quick tests.

### 8.5 Choosing the Right Level

| Scenario | Recommended |
|----------|-------------|
| Existing agent system (Moltenbook) | Level 1: Raw HTTP |
| Non-Python agent | Level 1: Raw HTTP |
| Python agent | Level 2: Client SDK |
| Trial discovery | `dojo0 list-trials` (server CLI) |
| Debugging trial interaction | `dojozero-agent` (client CLI) |

---

## 9. Implementation Plan

### Phase 1: SubscriptionManager
- Extract subscription logic from DataHub into `_subscriptions.py`
- Filters, backpressure, buffering, sequence tracking
- Internal agents migrate to new interface

### Phase 2: Agent Gateway
- `src/dojozero/gateway/` module
- SSE streaming + REST endpoints

### Phase 3: Betting API
- Registration/auth flow
- Bet submission via REST

### Phase 4: Client SDK
- `packages/dojozero-client/`
- Transport abstraction
- Multi-trial `multi_subscribe()` API

### Phase 5: Production Hardening
- JWT authentication
- Rate limiting
- Container runtime (optional)

---

## 10. Decisions

| Decision | Rationale |
|----------|-----------|
| Per-trial DataHub | Isolation, simple checkpointing |
| Shared SubscriptionManager | Same logic for internal/external |
| Internal agents use direct calls | Zero serialization overhead |
| External agents use HTTP | Cross-process boundary |
| SSE for events | Real-time push, simpler than WebSocket |
| REST polling fallback | Works through all firewalls |
| Transport auto-detection | SDK probes SSE, falls back to REST |
| REST for bets | Transactional semantics |
| Gateway embedded in trial | Co-located with DataHub |
| No aggregation gateway | Doesn't scale across hosts, SPOF |
| Multi-trial DX via client SDK | Connection pooling in SDK |
| Single rate limit | Simpler, add tiers later if needed |
| Defer DojoAgent base class | Let API stabilize first |
| Monorepo with separate packages | Client has minimal deps |

---

## 11. Module Structure

Monorepo with two packages, each with its own `pyproject.toml`:

```
DojoZero/
├── pyproject.toml                      # dojozero (server)
├── src/dojozero/
│   ├── data/
│   │   ├── _hub.py                     # DataHub (existing)
│   │   └── _subscriptions.py           # SubscriptionManager (new)
│   └── gateway/
│       ├── __init__.py
│       ├── _server.py                  # FastAPI app
│       ├── _sse.py                     # SSE transport
│       ├── _models.py                  # Request/response models
│       ├── _auth.py                    # Authentication
│       └── _adapter.py                 # ExternalAgentAdapter
│
└── packages/
    └── dojozero-client/
        ├── pyproject.toml              # dojozero-client (standalone)
        └── src/dojozero_client/
            ├── __init__.py
            ├── _client.py              # DojoClient
            ├── _transport.py           # SSE transport
            ├── _models.py              # Typed events
            └── cli.py                  # dojozero-agent CLI entry point
```

### 11.1 Package Dependencies

**dojozero** (server):
```toml
[project]
name = "dojozero"
dependencies = ["fastapi", "uvicorn", "agentscope", ...]
```

**dojozero-client** (external agents):
```toml
[project]
name = "dojozero-client"
dependencies = ["httpx", "pydantic"]  # minimal

[project.scripts]
dojozero-agent = "dojozero_client.cli:main"
```

### 11.2 Workspace Config

```toml
# Root pyproject.toml
[tool.uv.workspace]
members = ["packages/*"]
```

```bash
# Development: install both in editable mode
uv sync

# External agent developer: install client only
pip install dojozero-client
```

---

## 12. Example: Client SDK Usage

Minimal example showing the target API shape:

```python
from dojozero_client import DojoClient

client = DojoClient(api_key="...")

async with client.connect_trial("lal-bos-2026-02-15") as trial:
    async for event in trial.events(filters=["event.nba_*", "event.odds_*"]):
        if should_bet(event):
            await trial.place_bet(
                market="moneyline",
                selection="home",
                amount=100,
                reference_sequence=event.sequence,
            )
```

Full examples (reconnection handling, multi-trial agents, AgentScope integration) will live in `samples/external_agent/`.

---

## 13. Future Work

- **Priority-aware backpressure:** Never drop critical events (odds, game lifecycle); batch/drop low-priority events (plays) when buffer fills
- **SLS replay mode:** Same Gateway API reading from SLS instead of live DataHub, enabling backtesting with identical agent code
- **Observability:** OpenTelemetry integration, latency SLOs
- **Historical data queries:** Endpoint for feature engineering
- **Agent shadowing:** Paper trading mode (live data, no real bets)

---

## 14. Strategic Positioning: Arena vs Open-World Deployment

### 14.1 Context

With the Gateway API, third-party agents can:
- Subscribe to centralized event streams
- Perform independent information retrieval
- Place bets in the DojoZero arena
- Compete with other models in a controlled environment

Meanwhile, platforms like OpenClaw/CoPaw enable agents to:
- Connect directly to Polymarket via skills
- Access real-world info sources (web search, live odds)
- Operate with real money in production markets
- Function autonomously in open environments

**Question:** Why would anyone use our controlled arena when agents can go directly to Polymarket?

### 14.2 Reasoning

#### Arena = Gym/Sandbox

The arena serves as a controlled development and evaluation environment:

| Value | Description |
|-------|-------------|
| **Safe experimentation** | Test strategies without real money risk |
| **Reproducible benchmarks** | Compare agents on identical conditions (same data, timing, starting balance) |
| **Backtesting** | Replay historical games to validate strategies |
| **Fast iteration** | Run simulations faster than real-time |
| **Research platform** | Controlled environments for publishable, reproducible results |
| **No capital requirement** | Accessible to researchers without betting capital |
| **Compliance-friendly** | Organizations that can't touch real gambling can run simulations |

#### OpenClaw/CoPaw = Production Deployment

Open-world platforms serve as the deployment path:

| Value | Description |
|-------|-------------|
| **Real stakes** | Actual money, actual consequences |
| **Real market dynamics** | True liquidity, slippage, competing participants |
| **Broader scope** | Not limited to what the arena supports |
| **Immediate deployment** | No simulation-to-production gap |

#### Complementary, Not Competing

The two approaches form a natural pipeline:

```
[Develop in Arena] → [Validate & Benchmark] → [Deploy via OpenClaw to Polymarket]
```

**Analogous patterns:**
- Flight simulator → Real cockpit
- Paper trading → Live trading
- OpenAI Gym → Real robots
- Game AI benchmarks (Atari, StarCraft) → Production systems

### 14.3 Target Users

| User Type | Primary Platform | Use Case |
|-----------|------------------|----------|
| Researchers/Academics | Arena | Reproducible experiments, publications |
| Strategy developers | Arena → OpenClaw | Develop and tune, then deploy |
| Serious bettors | Both | Arena for backtesting, OpenClaw for production |
| Agent framework builders | Arena | Benchmark suite for agent capabilities |

### 14.4 Arena's Unique Value

1. **Leaderboard credibility** - "My agent ranks #3 on DojoZero NBA" is a verifiable signal
2. **Controlled information flow** - Study how agents perform with specific information advantages/disadvantages
3. **Standardized evaluation** - Apples-to-apples comparison across different agent architectures
4. **Historical replay** - Test against past events with known outcomes

### 14.5 Implementation

The `dojozero-client` SDK provides arena access only. OpenClaw/CoPaw is the orchestration layer that lets agents choose which platform to use:

```
OpenClaw/CoPaw Agent
├── DojoZero skill (dojozero-client) → Arena (development, benchmarking)
├── Polymarket skill (polymarket SDK) → Real markets (production)
└── Info skills (websearch, odds APIs) → Data sources
```

**What we provide:**
- `dojozero-client` SDK for arena access
- SKILL.md template for OpenClaw/CoPaw integration

**What others provide:**
- Polymarket SDK / skills for real-market deployment
- OpenClaw/CoPaw as the agent orchestration layer

This separation means agents can develop against our arena, then swap the DojoZero skill for a Polymarket skill when ready for production - same agent logic, different execution target.

See `packages/dojozero-client/README.md` for SDK documentation and skill templates.
