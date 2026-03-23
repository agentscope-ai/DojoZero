# Configuration Guide

This guide covers the current DojoZero configuration which includes:

1. **Environment variables** (`DOJOZERO_*` in `.env`)
2. **Trial configurations** (`trial_params/*.yaml`, `trial_sources/*.yaml`)
3. **Agent configurations** (`agents/llms`, `agents/personas`)


## 1. Environment Variables

### Core model and data keys

| Variable | Required | Purpose | Where to get it |
|---|---|---|---|
| `DOJOZERO_DASHSCOPE_API_KEY` | Required when using DashScope-backed models (Qwen/Deepseek) | LLM calls | DashScope account |
| `DOJOZERO_TAVILY_API_KEY` | Optional (required if pre-game web search stream is enabled) | Web search API | Tavily account |
| `DOJOZERO_X_API_BEARER_TOKEN` | Optional (required only if social media stream is enabled) | X/Twitter social signal ingestion | X developer account |


### Additional model/provider keys

| Variable | Required | Purpose |
|---|---|---|
| `DOJOZERO_ANTHROPIC_API_KEY` | If using Claude model config | Anthropic API access |
| `DOJOZERO_OPENAI_BASE_URL` | If using OPENAI model config | Custom OpenAI-compatible endpoint |
| `DOJOZERO_GEMINI_API_KEY` | If using Gemini model config | Gemini API access |
| `DOJOZERO_XAI_API_KEY` | If using Grok model config | Grok API access |

## `.env.example` and local setup

The repo includes `.env.example` as the canonical template.

```bash
cp .env.example .env
```

Then fill only the variables your scenario needs. Never commit real credentials.

### Python dependencies vs. features

- **Default install** (`uv pip install .`): core trials + **Jaeger** tracing. No OSS/SLS/Redis client libraries.
- **Alibaba / Redis extras** (`uv pip install '.[alicloud,redis]'`): needed for OSS, `--trace-backend sls`, and Redis-backed sync-service paths.

See [`installation.md`](./installation.md) for the full split.

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
Trial sources enable automatic game discovery and trial scheduling. When the server starts with --trial-source, it periodically syncs with sports APIs to find upcoming games and schedules trials to start before each game.

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

1. **Persona config** (`agents/personas/*.yaml`) - strategy style, tone, risk profile, and decision preferences.
2. **LLM config** (`agents/llms/*.yaml`) - model provider, model name, and API key environment mapping.

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
