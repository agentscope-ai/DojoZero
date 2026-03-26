# Full Configuration Guide

This guide covers DojoZero configuration in three areas:

1. **Environment variables** (`DOJOZERO_*` in `.env`)
2. **Trial configurations** (`trial_params/*.yaml`, `trial_sources/*.yaml`)
3. **Agent configurations** (`agents/llms`, `agents/personas`)


## 1. Environment Variables

### Search and social signals

| Variable | Required | Purpose | Where to get it |
|---|---|---|---|
| `DOJOZERO_TAVILY_API_KEY` | Optional (required if a trial enables the Tavily web-search stream) | Web search (Tavily) | [Tavily](https://tavily.com/) |
| `DOJOZERO_X_API_BEARER_TOKEN` | Optional (required if a trial enables the X/Twitter stream) | Social posts / signals | [X Developer Portal](https://developer.x.com/) |

### LLM provider keys

Set the keys that match your `agents/llms/*.yaml` provider choice. See `.env.example` for the full list (including optional Alibaba OSS/SLS variables).

| Variable | Required | Purpose | Where to get it |
|---|---|---|---|
| `DOJOZERO_DASHSCOPE_API_KEY` | When using DashScope-backed models (e.g. Qwen) in agent LLM config | LLM inference on DashScope | [DashScope](https://dashscope.aliyun.com/) |
| `DOJOZERO_ANTHROPIC_API_KEY` | When using Claude in agent LLM config | Anthropic Messages API | [Anthropic](https://www.anthropic.com/) |
| `DOJOZERO_OPENAI_API_KEY` | When using OpenAI or a compatible API that expects an API key | Bearer token for the provider | OpenAI or your compatible host |
| `DOJOZERO_OPENAI_BASE_URL` | When using a non-default OpenAI-compatible base URL | Overrides the default API base | Your provider’s docs |
| `DOJOZERO_GEMINI_API_KEY` | When using Gemini in agent LLM config | Google Gemini API | [Google AI / Gemini](https://ai.google.dev/) |
| `DOJOZERO_XAI_API_KEY` | When using Grok in agent LLM config | xAI API | [xAI](https://x.ai/) |

## `.env.example` and local setup

The repo includes `.env.example` as the canonical template.

```bash
cp .env.example .env
```

Then fill only the variables your scenario needs. Never commit real credentials.

### Python dependencies vs. features

- **Default install** (`uv pip install packages/dojozero`): core trials + **Jaeger** tracing. No OSS/SLS/Redis client libraries.
- **Alibaba / Redis extras** (`uv pip install 'packages/dojozero[alicloud,redis]'`): required for OSS, `--trace-backend sls`, and Redis-backed sync-service paths.

See [`getting-started.md`](./getting-started.md) for installation details.

Typical minimum for NBA/NFL trial usage:

```dotenv
DOJOZERO_DASHSCOPE_API_KEY=...
DOJOZERO_TAVILY_API_KEY=...
```


## 2. Trial Configuration (`trial_params/*.yaml`, `trial_sources/*.yaml`)

### Trial Parameter YAML (`trial_params/*.yaml`)

A trial params file defines one concrete, manually selected trial (single run).

```yaml
# trial_params/nba-moneyline.yaml
scenario:
  name: nba
  config:
    espn_game_id: "401810854"  # ESPN game ID
    hub:
      persistence_file: outputs/nba_prediction_events-{espn_game_id}.jsonl
    data_streams:
      # ... stream definitions
    operators:
      # ... operator definitions
    agents:
      # ... agent definitions
```

| Field | Purpose |
|---|---|
| `scenario.name` | Registered builder name (`nba`, `nfl`, or custom) |
| `scenario.config` | Builder-specific config payload |
| `scenario.config.espn_game_id` | Concrete ESPN game/event ID |
| `scenario.config.hub.persistence_file` | JSONL persistence output path |
| `scenario.config.data_streams` | Stream definitions and event subscriptions |
| `scenario.config.operators` | Operator instances and tool exposure |
| `scenario.config.agents` | Agent instances, persona, model config, subscriptions |

### Trial Source YAML (`trial_sources/*.yaml`)

Trial sources enable automatic game discovery and scheduling. When the server starts with `--trial-source`, it periodically syncs with sports APIs, discovers upcoming games, and schedules trials ahead of start time.

```yaml
# trial_sources/nba.yaml
source_id: nba-moneyline-source
sport_type: nba

config:
  scenario_name: nba
  scenario_config:
    # Same structure as trial params, but without game_id
    # (game_id is filled in automatically for each discovered game)
    hub: {}
    data_streams:
      # ... stream definitions
    operators:
      # ... operator definitions
    agents:
      # ... agent definitions

  # Scheduling options
  pre_start_hours: 2.0           # Start trial 2 hours before game
  check_interval_seconds: 60.0   # Check game status every 60 seconds
  auto_stop_on_completion: true  # Stop trial when game finishes
``` 

## 3. Agent Configuration (`agents/personas`, `agents/llms`)

- **Persona config** (`agents/personas/*.yaml`): strategy style, tone, risk profile, and decision preferences.
- **LLM config** (`agents/llms/*.yaml`): model provider, model name, and API key environment mapping.

### Persona configuration

Persona files define how an agent reasons and acts. Typical fields include:

- Role/identity text (what type of predictor/analyst the agent is)
- Risk appetite and bankroll behavior
- Decision heuristics and tool-usage style
- Output format conventions for consistency

You can create multiple personas and compare them under the same trial to evaluate behavioral differences.

### LLM provider configuration

LLM config files map agents to one or more providers/models.

Example file:

- `agents/llms/default.yaml`