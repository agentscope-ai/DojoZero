# Google Search Integration Flow

This document explains how Google search results fit into our data infrastructure models.

## Overview

The flow follows this pattern:
1. **Initiate Google Search** → `GoogleSearchResultEvent` (raw search results)
2. **Process with LLM** → `LLMSearchResultProcessor` (transforms event to fact)
3. **Generate Fact** → `SearchResultFact` (processed snapshot for operators)

## Data Models

### GoogleSearchResultEvent (Event)

**Type**: `DataEvent` with `update_type="snapshot"`

**Purpose**: Represents raw search results from Google Search API

**Key Fields**:
- `query`: Search query terms
- `results`: Raw Google API results (list of dicts)
- `total_results`: Number of results found
- `search_time`: Time taken for search
- `game_id`, `team_ids`, `player_ids`: Context for processing

**When Created**: When Google Search API is called

**Example**:
```python
event = GoogleSearchResultEvent(
    query="Lakers vs Celtics LeBron injury",
    search_id="search_123",
    results=[...],  # Raw Google API results
    total_results=10,
    search_time=0.45,
    game_id="game_123",
    timestamp=datetime.now(),
)
```

### SearchResultFact (Fact)

**Type**: `DataFact` (snapshot)

**Purpose**: Processed/aggregated search results that operators can consume

**Key Fields**:
- `summary`: LLM-generated summary
- `key_findings`: Key findings extracted by LLM
- `relevance_score`: Overall relevance (0.0-1.0)
- `sentiment`: Overall sentiment (positive/negative/neutral)
- `sentiment_score`: Numeric sentiment (-1.0 to 1.0)
- `betting_insights`: Betting-relevant insights
- `confidence`: Confidence in insights (0.0-1.0)
- `top_sources`: Top sources from search results

**When Created**: After LLM processing of `GoogleSearchResultEvent`

**Example**:
```python
fact = SearchResultFact(
    query="Lakers vs Celtics LeBron injury",
    search_id="search_123",
    summary="Recent news suggests LeBron is questionable...",
    key_findings=["LeBron questionable", "Lakers favored", ...],
    relevance_score=0.85,
    sentiment="negative",
    sentiment_score=-0.4,
    betting_insights=["Consider betting against Lakers", ...],
    confidence=0.75,
    timestamp=datetime.now(),
)
```

## Processing Flow

### Step 1: Initiate Google Search

```python
# In Data Store or Operator
search_query = "Lakers vs Celtics game tonight LeBron James injury status"
results = google_search_api.search(search_query)

# Create event
event = GoogleSearchResultEvent(
    query=search_query,
    search_id=generate_id(),
    results=results,
    total_results=len(results),
    game_id="game_123",
    timestamp=datetime.now(),
)
```

### Step 2: Process with LLM-Based Processor

```python
# Create processor
llm_client = OpenAI()  # or Anthropic(), etc.
processor = create_llm_search_processor(llm_client)

# Process event → fact
fact = processor.process(event)
```

**What LLM Processor Does**:
1. Extracts text from search results
2. Builds LLM prompt with query and results
3. Calls LLM to analyze and extract insights
4. Parses LLM response (JSON format)
5. Creates `SearchResultFact` with structured data

### Step 3: Operator Consumption

```python
# Operator pulls fact
fact = operator.pull_search_result(query="Lakers injury news", game_id="game_123")

# Agent uses fact for decision-making
if fact.sentiment_score < -0.3:
    # Negative sentiment - consider betting against
    pass
elif fact.confidence > 0.7:
    # High confidence insights - use for betting
    pass
```

## Integration Points

### Data Store
- **Stores**: `GoogleSearchResultEvent` (raw results) and `SearchResultFact` (processed)
- **Provides**: Query interface for operators to pull facts

### Data Processor
- **LLMSearchResultProcessor**: Transforms events to facts
- **Can be used by**: Data Streamers (push) or Operators (pull)

### Operator
- **Pulls**: `SearchResultFact` from Data Store
- **Uses**: Processed insights for agent decision-making
- **Returns**: Fact to agent (synchronous/blocking)

### Agent
- **Receives**: `SearchResultFact` from operator
- **Uses**: Insights, sentiment, betting recommendations
- **Decision**: Makes betting decisions based on processed insights

## Example: Complete Flow

```python
# 1. Agent requests search
agent.request_search("Lakers injury news", game_id="game_123")

# 2. Operator initiates search
operator.search("Lakers injury news", game_id="game_123")
# → Creates GoogleSearchResultEvent

# 3. Operator processes with LLM
processor = create_llm_search_processor(llm_client)
fact = processor.process(event)
# → Creates SearchResultFact

# 4. Operator returns fact to agent
agent.receive_fact(fact)

# 5. Agent uses fact for decision-making
if fact.betting_insights:
    agent.evaluate_betting_opportunity(fact)
```

## Key Design Decisions

1. **Event → Fact Transformation**: Raw search results (event) are processed into structured facts
2. **LLM Processing**: Uses LLM to extract insights, sentiment, and betting-relevant information
3. **Snapshot Pattern**: Search results are snapshots (not incremental updates)
4. **Operator Consumption**: Facts are designed for operator pull queries (synchronous)
5. **Structured Output**: LLM returns structured JSON that maps to `SearchResultFact` schema

## Benefits

- ✅ **Separation of Concerns**: Raw data (event) vs processed data (fact)
- ✅ **Reusability**: Processed facts can be cached and reused
- ✅ **Type Safety**: Strong typing with `SearchResultFact` schema
- ✅ **LLM Integration**: Leverages LLM for intelligent processing
- ✅ **Operator-Friendly**: Facts provide actionable insights for operators

