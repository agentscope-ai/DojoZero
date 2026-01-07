# DojoZero

AI agent system for real-time data reasoning and automated betting/trading. Agents run continuously on live data streams to analyze outcomes and take actions.

## Project Structure

```
src/dojozero/
├── core/           # Actor framework, runtime, dashboard orchestration
├── agents/         # AI agent implementations (BettingAgent, AgentGroup)
├── data/           # Data infrastructure (stores, events, processors, hub)
│   ├── nba/        # NBA game data (play-by-play, boxscores)
│   ├── polymarket/ # Prediction market odds
│   └── websearch/  # Web search with LLM processing
├── nba_moneyline/  # NBA betting scenario implementation
├── samples/        # Reference implementations (bounded_random)
├── ray_runtime/    # Distributed execution via Ray
└── cli.py          # CLI entry point
```

## Commands

```bash
# Run a trial
uv run dojozero run --params configs/nba-pregame-betting.yaml

# List available trial builders
uv run dojozero list-builders

# Generate example config for a builder
uv run dojozero get-builder nba-pregame-betting --example

# Replay from event files (backtesting)
uv run dojozero replay --events events.jsonl

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

### Trial System

Scenarios are registered as trial builders:

```python
@register_trial_builder("my-scenario", MyParamsModel)
def build_trial(trial_id: str, params: MyParamsModel) -> TrialSpec:
    ...
```

The `Dashboard` orchestrates actor wiring and lifecycle.

### Data Infrastructure

- **DataStore**: Query interface for domain data
- **DataEvent**: Typed events with `@register_event` decorator
- **DataProcessor**: Transforms raw events (e.g., LLM summarization)
- **DataHub**: Central event bus with persistence and subscriptions

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

## Key Patterns

1. **Registry Pattern**: Trial builders registered for CLI discovery
2. **Event Sourcing**: DataHub persists all events to JSONL for replay
3. **Checkpoint/Resume**: Actors serialize state for pause/resume
4. **Composition**: Agents register operators, streams register consumers

## Testing

Tests in `tests/` - use pytest with async support:

```bash
uv run pytest tests/test_specific.py -v
uv run pytest -k "test_name"
```

## Dependencies

- `agentscope`: AI agent framework
- `pydantic`: Configuration validation
- `nba_api`: NBA data
- `ray` (optional): Distributed runtime

## Additional Tips

### Pre-commit Hooks

Pre-commit runs automatically on commit:
- `ruff` - Linting with auto-fix
- `ruff-format` - Code formatting
- `pyright` - Type checking
- `pytest` - All tests must pass

Run manually: `uv run pre-commit run --all-files`

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

### Integration Tests

Mark with `@pytest.mark.integration`:
```bash
uv run pytest -m "not integration"  # Skip integration tests
uv run pytest -m integration         # Only integration tests
```

### Design Docs

Architecture decisions documented in `design/` with format `YYYY-MM-DD-title.md`. Once decided, docs are immutable - create revision docs for changes.

### Adding New Scenarios

1. Create package under `src/dojozero/`
2. Define Pydantic params model
3. Implement actors (DataStream, Operator, Agent)
4. Register trial builder with `@register_trial_builder`
5. Add YAML config in `configs/`
