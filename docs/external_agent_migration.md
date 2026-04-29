# External Agent Migration — Phase 0 + 1 Design

Status: **Design — approved, Phase 0 in implementation**.
Scope: gateway identity integration via the [`aip-identity-verify`](https://pypi.org/project/aip-identity-verify/) library (talks to an [aip-idp](https://github.com/agentscope-ai/agent-identity) instance, e.g. `https://pre.agent-id.live`), and a single-image canary runner using [`aip-identity-sdk`](https://pypi.org/project/aip-identity-sdk/) that externalizes the `daily`-tier agent. Deployment plumbing (Aone app registration, runner Dockerfile, compose entries) is intentionally deferred to a sibling doc in `DojoZeroDeploy`.

> **Auth header note**: AIP uses `Authorization: AIP <token>` — **not** `Bearer`. This is the scheme `aip-identity-sdk`'s `AIPClient` sends (`client.py:78`) and `aip-identity-verify`'s `AIPVerifier.verify` accepts (`verifier.py:200`).

## Why now, why scoped

The gateway and `dojozero-client` SDK already support external agents (see `docs/client.md`). What's missing:

1. **Identity is trust-based.** External agents authenticate via an `X-Agent-ID` header backed by a YAML-loaded API key (`AgentKeyManager` in `gateway/_auth.py`). aip-idp gives us proper crypto-backed identity (`aip:<domain>:<uuid>`, Ed25519 keys, JWT issuance, JWKS verification) with the same model the rest of the org is moving to.
2. **No reference internal agent runs externally.** Today's external-agent contract is exercised only by `demos/external_agent/robust_agent.py` (a hardcoded threshold bettor — no LLM). We don't have a real proof point that the in-process `BettingAgent` paradigm works end-to-end through the gateway.

Phase 0+1 fixes both with the smallest credible change:

- **Phase 0**: gateway accepts JWTs minted by aip-idp, alongside the existing header path.
- **Phase 1**: one canary external agent — the `daily` tier's `degen × claude` — runs as a separate container against the gateway, using its aip-idp identity.

A "farm orchestrator", per-LLM secret distribution, multi-tenant isolation, and broader matrix migration are explicitly **out of scope**. We expect to defer them until a Phase 1 trigger fires (see [Triggers for Phase 2+](#triggers-for-phase-2)).

## Non-goals

- Migrating the full 7×6 persona×LLM matrix.
- Building a farm orchestrator daemon.
- Replacing in-process agents in `pre`/`prod` tiers.
- Replacing the `X-Agent-ID` header path. It stays during the transition; we deprecate it only after the JWT path proves stable in the canary.

## Architecture

```
                                 ┌────────────────────────────────┐
                                 │ aip-idp                        │
                                 │  POST /aip/agents              │
                                 │  POST /aip/token               │
                                 │  GET  /.well-known/aip-jwks    │
                                 └────────────────────────────────┘
                                        ▲                ▲
              register / mint JWT       │                │   fetch JWKS (cached 1h)
                                        │                │
┌─────────────────────┐                 │                │       ┌──────────────────────┐
│ runner container    │─────────────────┘                └───────│ DojoZero gateway     │
│  (degen × claude)   │                                          │  (existing)          │
│                     │                                          │                      │
│  - aip-idp client   │                                          │  - Bearer JWT verify │
│  - Ed25519 keypair  │   GET /events/recent?since=…             │  - JWKS client       │
│  - DojoClient (SDK) │ ───────────────────────────────────────▶ │  - falls back to     │
│  - BettingAgent loop│   POST /bets, /balance, /odds            │    X-Agent-ID header │
│                     │                                          │                      │
│  env-driven config: │                                          │                      │
│   PERSONA, LLM,     │                                          │                      │
│   TRIAL_ID,         │                                          │                      │
│   AGENT_ID,         │                                          │                      │
│   GATEWAY_URL,      │                                          │                      │
│   AIP_IDP_URL       │                                          │                      │
└─────────────────────┘                                          └──────────────────────┘
```

## Phase 0 — gateway AIP integration

### What changes

We **do not roll our own JWKS cache or JWT verifier**. The work is wiring `aip-identity-verify` into the gateway and adding a parallel auth path.

New file: `packages/dojozero/src/dojozero/gateway/_aip.py`
- Thin module exposing `aip_verifier_from_env() -> AIPVerifier | None`.
- Returns `None` if AIP not configured, raises if configured but the optional dep is missing.
- Keeps the import of `aip_identity_verify` lazy so dojozero works without the dep installed.

`packages/dojozero/src/dojozero/gateway/_server.py`
- `GatewayState` gains an optional `aip_verifier: AIPVerifier | None` field.
- `create_gateway_app()` accepts an `aip_verifier` parameter and stores it on state.
- `get_agent_id()` becomes `async`. Order of precedence per request:
  1. `Authorization: AIP <token>` — if `aip_verifier` is configured, call `verifier.verify_token(token)`, return `agent.agent_id`.
  2. `X-Agent-ID: <id>` — existing path; stays valid through the canary.
  3. Otherwise → 401.
- Existing `AuthProvider`/`validate_jwt` plumbing in `_auth.py` is untouched. AIP is its own parallel path; the legacy Bearer-JWT plumbing (which is unused) stays where it is for future cleanup.

### Configuration

New env vars on the dashboard server / gateway:

| Env | Required | Purpose |
|---|---|---|
| `DOJOZERO_AIP_TRUSTED_PROVIDERS` | yes (when AIP enabled) | Comma-separated list of trusted IdP domains (e.g., `pre.agent-id.live`). |
| `DOJOZERO_AIP_AUDIENCE` | yes (when AIP enabled) | Expected `aud` claim — the gateway's public origin (e.g., `https://api.dojozero.live`). |
| `DOJOZERO_AIP_CACHE_TTL_SECONDS` | optional, default `3600` | JWKS cache TTL passed to `AIPVerifier`. |
| `DOJOZERO_AIP_CLOCK_SKEW_SECONDS` | optional, default `30` | Clock skew tolerance for `exp` validation. |
| `DOJOZERO_AIP_PROVIDER_URLS` | optional | JSON object mapping domain → base URL, for local-dev / non-https IdP overrides. |

If neither `DOJOZERO_AIP_TRUSTED_PROVIDERS` nor `DOJOZERO_AIP_AUDIENCE` are set, AIP is disabled and the gateway runs on the legacy header path only — no behaviour change.

### Optional dependency

`aip-identity-verify` is added as an extra: `pip install dojozero[aip]` or `uv sync --extra aip`. The gateway runs without it; AIP just stays disabled.

### Mapping `aip:<domain>:<uuid>` → internal agent

The existing `AgentKeyManager` keys agents by API-key-derived `agent_id`. For AIP-authed requests, the `sub` claim *is* the agent identity directly (`aip:<domain>:<uuid>`); we do **not** require a pre-registered API key, but we do require the agent to be registered for the active trial via the existing `POST /agents` flow. Phase 0 keeps `POST /agents` unchanged; the AIP token just carries the identity instead of the header.

### Backwards-compat

Both auth paths run side-by-side. There is no flag day. Once the canary has run cleanly for N trials (see [Success criteria](#success-criteria)), we open a second PR to log a deprecation warning when `X-Agent-ID` is presented without an AIP token. We do **not** remove the header path in this phase.

### Tests

- Unit: `aip_verifier_from_env()` reads env correctly; partial config logs warning and returns None; missing dep raises clearly.
- Unit: `get_agent_id` with stubbed `AIPVerifier` — happy path returns `agent_id`; expired/invalid/untrusted-provider tokens raise 401 with the right `ErrorCode`; falls through to header path when no `Authorization` header.
- Integration (deferred — needs a reachable IdP): hit a real `pre.agent-id.live` instance, mint a token via `aip-identity-sdk`, call `/events/recent`, assert 200.

## Phase 1 — canary runner

### Package layout

New package: `packages/dojozero-agent-runner/`. We do **not** write our own AIP client or identity loader — `aip-identity-sdk` provides both.

```
packages/dojozero-agent-runner/
├── pyproject.toml                          # depends on dojozero-client, aip-identity-sdk, agentscope
└── src/dojozero_agent_runner/
    ├── __init__.py
    ├── __main__.py                         # entry: `python -m dojozero_agent_runner`
    ├── _config.py                          # env → RunnerConfig
    ├── _runner.py                          # main loop: SDK polling + ReActAgent decisions
    └── _tools.py                           # SDK-method-backed tool adapters
```

A new console-script entry `dojozero-agent-runner` is exposed via `pyproject.toml`.

Identity is loaded via `AIPIdentity.from_env()` (uses `AIP_AGENT_ID`, `AIP_AGENT_KID`, `AIP_PRIVATE_KEY`, `AIP_IDP_URL`). Token minting + caching + 401-retry is delegated to `AIPClient` from `aip-identity-sdk`. The runner doesn't touch JWTs directly.

### Configuration

The runner is a single image; everything is env-driven so the same image runs for any persona×LLM. AIP-related env vars use the SDK's native `AIP_*` names (let the SDK own its env contract):

| Env | Required | Example |
|---|---|---|
| `DOJOZERO_PERSONA` | yes | `degen` |
| `DOJOZERO_LLM` | yes | `claude` (key into `agents/llms/default.yaml`) |
| `DOJOZERO_PERSONA_PATH` | optional | `/agents/personas/degen.yaml` (default mounted location) |
| `DOJOZERO_LLM_CONFIG_PATH` | optional | `/agents/llms/default.yaml` |
| `DOJOZERO_TRIAL_ID` | yes | `trial_2026_04_29_lakers_warriors` |
| `DOJOZERO_GATEWAY_URL` | yes | `https://api.dojozero.live` (used as default audience for AIP tokens) |
| `AIP_AGENT_ID` | yes | `aip:dojozero:degen-claude-canary` |
| `AIP_AGENT_KID` | yes | `<sha256(pubkey)[:16]>` |
| `AIP_PRIVATE_KEY` | yes | hex-encoded 32-byte Ed25519 seed |
| `AIP_IDP_URL` | optional | `https://pre.agent-id.live` — derived from `AIP_AGENT_ID` if unset |
| `DOJOZERO_POLL_INTERVAL_SECONDS` | optional, default `5` | |
| `DOJOZERO_<PROVIDER>_API_KEY` | yes (one) | e.g. `DOJOZERO_ANTHROPIC_API_KEY` for Claude |

Persona and LLM YAMLs are read from a mounted volume. They are **not** baked into the image — the same image must work across persona/LLM combinations. The deploy repo handles the mount.

### Identity bootstrapping

On startup the runner:

1. Loads its identity via `AIPIdentity.from_env()` (`aip-identity-sdk`).
2. Constructs `AIPClient(identity, default_audience=DOJOZERO_GATEWAY_URL)`. The client lazily mints tokens on first use and caches per-audience with a 60s safety margin before `exp` (`client.py:32-37`).
3. Calls `POST /agents` on the dashboard-server gateway to register itself for the trial (existing endpoint, now AIP-authed).
4. Connects via `DojoClient.connect_trial(...)`, with the client's `GatewayTransport` extended to delegate auth-header generation to the `AIPClient`.

Agent + key registration with the IdP itself (`POST /aip/agents`) is a one-time provisioning step done **outside the runner** — likely via the `aip-identity-cli` tool or a management portal. The runner never holds a management token; it only holds the agent's private key (raw 32-byte Ed25519 seed in `AIP_PRIVATE_KEY`).

### Runtime loop

The runner replicates `BettingAgent`'s decision loop, but with operator calls replaced by SDK methods:

| In-process `BettingAgent` call | Runner equivalent |
|---|---|
| `broker_op.place_bet(...)` | `connection.place_bet(...)` |
| `broker_op.get_balance()` | `connection.get_balance()` |
| `social_board_op.post_message(...)` | `connection.post_social_message(...)` (extends SDK) |
| `data_stream.events()` (in-process Stream) | `connection.poll_events(since=...)` |
| `register_operators(deps)` | tool registry built from SDK method bindings |

The ReActAgent loop itself (AgentScope) is unchanged — it's only the tool implementations that get swapped. `agentscope` stays as a runner dependency; the runner image carries it. The runner does **not** depend on the rest of the `dojozero` package — only on `dojozero-client`.

### Cluster routing

The gateway's `_gateway_routing.py` may 307-redirect to the trial's `owner_server_id`. `dojozero-client`'s `GatewayTransport` follows redirects via httpx defaults but we must verify:

- Auth headers are **re-attached** after redirect (httpx strips them by default on cross-origin redirects).
- The cached AIP token is still valid for the new `aud`. With `default_audience` set on `AIPClient`, the same token is reused regardless of redirect target — fine if the cluster shares one audience. If sport-specific audiences are introduced later, the client picks audience from URL origin (`client.py:100-104`), in which case it transparently mints a fresh token for the new origin.

If httpx default behavior is wrong, we override transport-level redirect handling. This is verified in Phase 1 implementation, not assumed.

### Concurrency with in-process agents

For the canary, the same `degen × claude` runs in two places:

- **In-process** (existing `daily` tier dashboard container) — continues to bet on its own account.
- **External runner** (new container) — registered as a separate `aip:dojozero:degen-claude-canary` agent with its own broker account.

This gives us a side-by-side comparison without disrupting `daily`-tier output. After Phase 1 graduates, the in-process instance is disabled in `trial_sources/daily/` and the external runner takes over.

## Success criteria

The canary is "good enough to ramp up" when **all** of these hold across N=5 consecutive trials:

| Gate | Threshold |
|---|---|
| Reaction latency | external p50 ≤ in-process p50 + 6s; p95 ≤ in-process p95 + 8s |
| Reliability | 0 unhandled crashes; ≤ 1 transient gateway error self-recovered per trial |
| Decision parity | bet count within ±15% of in-process baseline; bet-direction agreement ≥ 70% on shared events |
| Trace completeness | every external-agent decision has a matching SLS span; trace IDs link runner → gateway → broker |
| Auth correctness | 0 successful requests with expired / invalid JWT in gateway logs |

If any gate fails, we stop expanding and fix the regression before bringing in additional persona/LLM combinations.

## Triggers for Phase 2+

We do **not** build a farm orchestrator now. We commit to building one only when one of these fires:

- Manual lifecycle (compose up / down per trial) becomes operationally painful — defined as: an on-call incident attributable to manual runner lifecycle, or > 30 min/week spent managing it.
- We accept third-party / community agents on the gateway as a product feature (forcing function: same path internally and externally).
- An LLM SDK regression OOMs the canary container in a way per-agent isolation would have prevented.
- We want runtime heterogeneity (Codex, OpenClaw / QwenPaw, human-in-the-loop) — the farm becomes the natural placement boundary.

Until one of those triggers, we expand the canary by adding services to `docker-compose.yml` (or sibling Aone apps via the deploy repo, depending on environment). One image, N env-configured instantiations.

## Open questions

These need answers before implementation lands:

1. **aip-idp dev/prod URL** — what hostnames does dashboard-server / runner use in `daily` vs `pre` vs `prod`?
2. **Audience claim** — single `dojozero-gateway` audience across sports, or per-sport (`dojozero-gateway-nba` etc)?
3. **Private key distribution** — env var, file mount, or Aone secret manager for `DOJOZERO_AGENT_PRIVATE_KEY`? (Same question for the LLM provider key.)
4. **Provisioning workflow** — who runs `POST /aip/agents` to mint each runner's identity? CLI script, manual portal, or automated at deploy time?
5. **Canary account scoping** — does the broker treat `aip:dojozero:degen-claude-canary` as a brand-new account (initial balance, separate P&L), or does it inherit the in-process agent's history? Recommend: brand-new account so comparisons are clean.

These are deliberately deferred to the implementation review — they're operational decisions, not architectural ones.

## File-level change summary (DojoZero only)

Added:
- `docs/external_agent_migration.md` (this file)
- `packages/dojozero/src/dojozero/gateway/_aip.py` — env-driven `AIPVerifier` factory
- `packages/dojozero-agent-runner/` (new package, Phase 1)
- Tests: `packages/dojozero/tests/gateway/test_aip_auth.py` and `packages/dojozero-agent-runner/tests/`

Modified:
- `packages/dojozero/pyproject.toml` — add `aip` extra with `aip-identity-verify`
- `packages/dojozero/src/dojozero/gateway/_server.py` — `aip_verifier` field on `GatewayState`, async `get_agent_id` with AIP scheme support, `create_gateway_app` accepts verifier
- `packages/dojozero-client/src/dojozero_client/_transport.py` — accept optional `AIPClient`, delegate auth-header generation when present (Phase 1)
- Top-level workspace `pyproject.toml` — register `dojozero-agent-runner` (Phase 1)

No changes to:
- `packages/dojozero/src/dojozero/gateway/_auth.py` — `AuthProvider` and its unused legacy JWT plumbing stay as-is. AIP is implemented as a parallel auth path in `_server.py:get_agent_id` and `_aip.py`.
- `packages/dojozero/src/dojozero/agents/`, `betting/`, `nba/`, `nfl/`, `ncaa/` — in-process agents continue to work unchanged.
- `trial_params/` — the canary is configured outside the trial spec for now (separate registration via `POST /agents`).
- Any deployment files — those land in DojoZeroDeploy after this design is approved and the code changes ship.
