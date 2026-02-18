# Trial Containerization and API-Centric Agent Integration

**Date**: 2026-02-17
**Status**: Draft

---

## Executive Summary

Enable third-party agents to participate in DojoZero trials via HTTP/WebSocket APIs. Trials remain isolated units with per-trial DataHub; agents can subscribe to multiple trials simultaneously.

**Key decisions:**
- Trial-level containerization (not per-agent)
- Gateway embedded in trial (process or container)
- Per-trial DataHub (no centralized event bus)
- Agents manage their own multi-trial state

---

## 1. Goals

| Goal | Priority |
|------|----------|
| Third-party agents in any language | High |
| Sub-100ms added latency for betting | High |
| Backwards compatibility with internal agents | High |
| Clean trial lifecycle management | Medium |

**Non-goals (this phase):** Per-agent containerization, event mesh (NATS/Kafka), multi-region.

---

## 2. Architecture

### 2.1 Per-Trial Gateway

Each trial runs its own Gateway, whether as a process or container:

```
┌─────────────────────────────────────────────────────────┐
│  Trial (Process or Container)                           │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │ TrialOrchestrator + DataHub + Agents            │   │
│  └────────────────────────┬────────────────────────┘   │
│                           │                             │
│  ┌────────────────────────▼────────────────────────┐   │
│  │ Agent Gateway (:8080)                           │   │
│  │ REST API | WebSocket Hub | Subscription Mgr     │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
              ▲                    ▲
         External Agent 1    External Agent 2
```

### 2.2 Deployment Modes

| Mode | Gateway Location | How to Run |
|------|------------------|------------|
| **Local dev** | Embedded in trial process | `dojo0 run --enable-gateway --gateway-port 8080` |
| **Multi-trial local** | Each trial on different port | Multiple `dojo0 run` with different ports |
| **Dashboard Server** | Centralized, routes by trial_id | `dojo0 serve --enable-gateway` |
| **Container (prod)** | Embedded in container | Reverse proxy routes to containers |

**Local dev example:**
```bash
# Single trial
dojo0 run --params trial.yaml --enable-gateway

# External agent connects to localhost:8080
```

**Dashboard Server mode:**
```bash
dojo0 serve --enable-gateway --trace-backend jaeger

# External agents use:
# GET  http://localhost:8000/api/gateway/{trial_id}/trial
# WS   ws://localhost:8000/ws/gateway/{trial_id}/events
```

### 2.3 Component Summary

| Component | Purpose | New/Existing |
|-----------|---------|--------------|
| Agent Gateway | HTTP/WebSocket API for external agents | New |
| ExternalAgentAdapter | Bridges API calls to Actor protocol | New |
| DataHub | Per-trial event bus (unchanged) | Existing |
| BrokerOperator | Bet execution | Existing |

---

## 3. Multi-Trial Agent Pattern

Agents can subscribe to multiple trials simultaneously. Each trial is independent; agents manage their own cross-trial state.

```
┌─────────────────────────────────────────────────────────────┐
│                      External Agent                          │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Event Router (routes by trial_id/game_id)              │ │
│  └──────────┬─────────────────┬─────────────────┬─────────┘ │
│             ▼                 ▼                 ▼           │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────┐ │
│  │ Trial A State    │ │ Trial B State    │ │ Trial C State│ │
│  │ balance, bets    │ │ balance, bets    │ │ balance, bets│ │
│  └──────────────────┘ └──────────────────┘ └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
        │ WS                  │ WS                  │ WS
        ▼                     ▼                     ▼
   Trial A Gateway      Trial B Gateway      Trial C Gateway
```

**Agent responsibilities:**
- Maintain N connections (one per trial)
- Route events by `trial_id` or `game_id`
- Track state per trial
- Place bets via correct trial's API

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

### 4.2 WebSocket Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/ws/v1/events` | Real-time event stream |
| `/ws/v1/agent/{agent_id}` | Agent notifications (settlements) |

### 4.3 Message Format

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

| Tier | Requests/min | WebSocket connections |
|------|--------------|----------------------|
| Free | 60 | 1 |
| Standard | 300 | 3 |
| Premium | 1000 | 10 |

### 5.4 Outcome Gaming Prevention

1. Reject bets where `|client_ts - server_ts| > 5s`
2. Bets must reference latest event sequence
3. `BettingOperator.can_bet` checks event status

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

| Buffer Depth | Action |
|--------------|--------|
| < 100 | Normal |
| 100-500 | Batch play events |
| 500-1000 | Drop low-priority |
| > 1000 | Disconnect with resumption token |

**Priority:** Critical (lifecycle, odds) > High (game updates) > Normal (plays)

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
| WebSocket disconnect | Sequence tracking, replay |
| Bet timeout | Idempotency keys |
| Trial crash | Health checks, notifications |

---

## 8. Implementation Plan

### Phase 1: Agent Gateway MVP
- `src/dojozero/gateway/` module
- WebSocket event streaming
- REST endpoints for trial info

### Phase 2: Betting API
- Registration/auth flow
- Bet submission
- Settlement notifications

### Phase 3: Container Runtime (optional)
- Dockerfile, entrypoint
- Health checks
- Docker Compose

### Phase 4: Production Hardening
- JWT authentication
- Rate limiting
- Audit logging

---

## 9. Decisions

| Decision | Rationale |
|----------|-----------|
| Per-trial DataHub | Isolation, simple checkpointing |
| Gateway embedded in trial | Co-located with DataHub, natural lifecycle |
| WebSocket for events | Real-time push |
| REST for bets | Transactional semantics |
| Agents manage multi-trial state | No central coordination needed |

---

## 10. Module Structure

```
src/dojozero/gateway/
├── __init__.py
├── _server.py          # FastAPI app
├── _registry.py        # Agent registration
├── _websocket.py       # WebSocket handlers
├── _models.py          # Request/response models
├── _auth.py            # Authentication
└── _adapter.py         # ExternalAgentAdapter
```
