# DojoZero

AI agent system for real-time data reasoning and automated betting/trading. Agents run continuously on live data streams to analyze outcomes and take actions.

See [README.md](./README.md) for installation and CLI usage.

## Project Structure

```
src/dojozero/
├── core/              # Actor framework, runtime, trial orchestration
├── agents/            # AI agent implementations (BettingAgent, AgentGroup)
├── data/              # Data infrastructure (stores, events, processors, hub)
│   ├── nba/           # NBA game data (play-by-play, boxscores)
│   ├── nfl/           # NFL game data
│   ├── espn/          # ESPN data integration
│   ├── polymarket/    # Prediction market odds
│   └── websearch/     # Web search with LLM processing
├── betting/           # Shared betting utilities
├── nba_moneyline/     # NBA moneyline betting scenario
├── nfl_moneyline/     # NFL moneyline betting scenario
├── nfl_game/          # NFL game data support
├── samples/           # Reference implementations (bounded_random)
├── dashboard_server/  # Trial orchestration server
├── arena_server/      # Web UI server
├── ray_runtime/       # Distributed execution via Ray
├── utils/             # Shared utilities
└── cli.py             # CLI entry point
```

## Development Commands

```bash
# Run tests
uv run pytest

# Type checking
uv run pyright

# Linting
uv run ruff check src/
```

## Architecture

### Actor Model

All components implement the `Actor` protocol with lifecycle methods:
- `from_dict()` - Instantiate from config
- `start()` / `stop()` - Lifecycle management
- `save_state()` / `load_state()` - Checkpointing

Three actor types:
- **DataStream**: Publishes `StreamEvent` to consumers
- **Operator**: Handles synchronous queries, stateful operations
- **Agent**: Consumes streams, makes decisions, calls operators

### Data Infrastructure

- **DataStore**: Query interface for domain data
- **DataEvent**: Typed events with `@register_event` decorator
- **DataProcessor**: Transforms raw events (e.g., LLM summarization)
- **DataHub**: Central event bus with persistence and subscriptions

## Key Patterns

1. **Registry Pattern**: Trial builders registered for CLI discovery
2. **Event Sourcing**: DataHub persists all events to JSONL for backtesting
3. **Checkpoint/Resume**: Actors serialize state for pause/resume
4. **Composition**: Agents register operators, streams register consumers

## Code Conventions

### Events and Models

- Use `@dataclass(slots=True, frozen=True)` for immutable events
- Include `from_dict()` / `to_dict()` for API compatibility (camelCase keys)
- Register events with `@register_event` decorator

### State Management

- Encapsulate related state in tracker classes (see `GameStateTracker`)
- All state must be JSON-serializable for checkpointing

### Processors

- Inherit from `BaseDashscopeProcessor` for LLM-based processing
- Set `intended_intent` and `fallback_keywords` class attributes
- Implement `process()` method

### Configuration

- Use Pydantic models for validation
- Environment variables use `DOJOZERO_` prefix
- YAML configs in `configs/` directory

### Module Naming

- Private modules use underscore prefix: `_store.py`, `_events.py`, `_api.py`
- Public API exposed via `__init__.py` with explicit `__all__`

### Logging

Use module-level logger:
```python
import logging
logger = logging.getLogger(__name__)
```

### Async Patterns

- Actors use `async def start()`, `async def stop()`
- Data fetching uses `aiohttp` for async HTTP
- Tests use `pytest-asyncio` with `@pytest.mark.asyncio`

## Testing

```bash
uv run pytest tests/test_specific.py -v
uv run pytest -k "test_name"
uv run pytest -m "not integration"  # Skip integration tests
uv run pytest -m integration         # Only integration tests
```

Mark integration tests with `@pytest.mark.integration`

## Pre-commit Hooks

Pre-commit runs automatically on commit:
- `ruff` - Linting with auto-fix
- `ruff-format` - Code formatting
- `pyright` - Type checking
- `pytest` - All tests must pass

Run manually: `uv run pre-commit run --all-files`

## Design Docs

Architecture decisions documented in `design/` with format `YYYY-MM-DD-title.md`
