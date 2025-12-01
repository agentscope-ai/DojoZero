# Data Processors

This module contains all data processing logic for transforming events into facts.

## Structure

- **`_aggregators.py`**: Aggregators that convert events to facts
  - Stateful aggregators (maintain state for streaming)
  - Stateless aggregators (batch processing)
- **`_processors.py`**: LLM-based and other processors
  - LLM processors for transforming events using language models

## Separation of Concerns

- **`data/`**: Data models/schemas only (`DataFact`, `DataEvent` types)
- **`processor/`**: Processing logic (aggregators, LLM processors, transformations)

This separation provides:
- Clear boundaries between data structures and processing logic
- Easier to maintain and test
- Better organization and discoverability

## Usage

### Aggregators

```python
from agentx.processor import create_score_aggregator, create_stateless_score_aggregator

# Stateful aggregator (for streaming)
stateful_agg = create_score_aggregator()
fact = stateful_agg.update(event)  # Updates state incrementally

# Stateless aggregator (for batch processing)
stateless_agg = create_stateless_score_aggregator()
facts = stateless_agg.aggregate(events)  # Processes batch
```

### LLM Processors

```python
from agentx.processor import create_llm_search_processor
from agentx.data import GoogleSearchResultEvent

# Create processor
llm_client = OpenAI()  # or Anthropic(), etc.
processor = create_llm_search_processor(llm_client)

# Process event → fact
event = GoogleSearchResultEvent(...)
fact = processor.process(event)  # Returns SearchResultFact
```

## See Also

- `data/` - Data models and schemas
- `data/GOOGLE_SEARCH_FLOW.md` - Google search integration flow

