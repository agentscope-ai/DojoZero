# Background Cache Refresh for Arena Server

**Date**: 2026-02-05
**Status**: Draft

## Motivation

The Arena Server queries SLS/Jaeger on every cache miss. This causes:

1. **Cache stampede** -- When TTL expires, concurrent requests all hit SLS simultaneously
2. **User-visible latency** -- First request after expiration waits 2-5s for SLS
3. **Cold start penalty** -- After restart, all users see slow responses
4. **Unpredictable SLS load** -- Query volume scales with user traffic

## Design Overview

Proactively fetch all data in the background. User requests only read from cache.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Arena Server                                  │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                    Background Tasks                                │  │
│  │                                                                    │  │
│  │  REST Cache Refresher          Live Stream Managers               │  │
│  │  (every 15-30s)                (1 per live trial, ~10 max)        │  │
│  │                                                                    │  │
│  │  - trials_list                 - polls SLS every 1s               │  │
│  │  - stats (global/NBA/NFL)      - caches recent spans              │  │
│  │  - games (global/NBA/NFL)      - broadcasts to viewers            │  │
│  │  - leaderboard                                                    │  │
│  │  - agent_actions                                                  │  │
│  └──────────────────────────┬────────────────────────────────────────┘  │
│                             │                                           │
│                        SLS / Jaeger                                     │
│                             │                                           │
│  ┌──────────────────────────┴────────────────────────────────────────┐  │
│  │                         Caches                                     │  │
│  │                                                                    │  │
│  │  REST Cache              Live Stream Cache       Replay Cache      │  │
│  │  (stats, games, etc.)    (per-trial spans)       (completed trials)│  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                             ▲                                           │
│                        reads only                                       │
│  ┌──────────────────────────┴────────────────────────────────────────┐  │
│  │                      User Requests                                 │  │
│  │                                                                    │  │
│  │  GET /api/stats         WS /stream              WS /replay         │  │
│  │  GET /api/games         (subscribe to           (playback from     │  │
│  │  GET /api/leaderboard    live stream)            replay cache)     │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## REST API Caching

Background task refreshes all REST caches on a fixed interval (15-30s).

**What gets cached:**

| Cache | Partitions | Notes |
|-------|-----------|-------|
| `trials_list` | global | Foundation for other caches |
| `stats` | global, NBA, NFL | Aggregated metrics |
| `games` | global, NBA, NFL | Live/upcoming/completed |
| `leaderboard` | global, NBA, NFL | Agent rankings |
| `agent_actions` | global, NBA, NFL | Recent actions ticker |

Per-league partitions exist because endpoints support `?league=NBA` filtering.

**Behavior:**
- Server blocks on initial cache population at startup
- On refresh failure, keep serving stale data
- Refresh all partitions in parallel for speed

## Live WebSocket Streaming

Proactively stream all live trials (max ~10 at any time). Don't wait for first viewer.

**How it works:**

1. `LiveTrialRegistry` discovers live trials from `trials_list` cache
2. Creates a `TrialStreamManager` for each live trial
3. Each manager polls SLS every 1s, caches recent spans (last 5 min)
4. When viewer connects, they subscribe to the manager and get cached snapshot
5. New spans are broadcast to all subscribers
6. When trial completes, manager stops and trial moves to replay cache

**Per-connection state:**
- Each viewer has a `StreamController` for pause/resume
- Paused viewers buffer incoming spans; on resume, buffer is flushed
- Buffer capped at 1000 spans to bound memory

## Replay Cache

Proactively load replay data for completed trials.

**Loading strategy:**
- On startup: Load recent completed trials (last 24h, max 50)
- On trial completion: Automatically cache full span history

**Cache characteristics:**
- TTL: Long (1 hour+) since completed trials never change
- Eviction: LRU with max 100 entries
- Fallback: Old trials outside window fetched on-demand

## Startup Sequence

```
1. Fetch trials_list from SLS
2. In parallel:
   a. Populate REST caches (stats, games, leaderboard, actions)
   b. Load replay data for recent completed trials
   c. Start TrialStreamManagers for live trials
3. Accept requests
```

Server blocks until step 2 completes (with timeout). This ensures first user request is instant.

## Configuration

| Parameter | Default | Purpose |
|-----------|---------|---------|
| REST refresh interval | 15-30s | How often to refresh REST caches |
| Live stream poll interval | 1s | How often to poll SLS for live trials |
| Replay lookback | 24h | How far back to load completed trials |
| Max replay entries | 100 | LRU cache size for replay data |
| Startup timeout | 30s | Max wait for initial cache warm |

## Error Handling

| Scenario | Behavior |
|----------|----------|
| REST refresh fails | Log warning, keep stale data, retry next interval |
| Live stream poll fails | Log warning, retry next interval |
| Startup timeout | Start with partial cache, continue warming in background |

## Alternatives Considered

**Stale-while-revalidate**: Serve stale data while refreshing in background on cache miss.
Rejected: Still has thundering herd on first miss. Pure background refresh is simpler.

**Compute per-league from global**: Fetch all data globally, filter in-memory for league queries.
Worth considering: Would reduce SLS queries. Trade-off is filtering complexity.

**Redis cache**: External shared cache for horizontal scaling.
Rejected: Additional infrastructure. Single instance sufficient for current scale.
