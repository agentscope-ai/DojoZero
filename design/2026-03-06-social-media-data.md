# Social Media DataStream Design

## Design

1. **Account-Based Tracking**: Track specific curated accounts (team accounts, beat reporters, betting analysts)
2. **X API Integration**: Uses official X API (xdk) to search recent posts from watchlist accounts
3. **Summarization**: All tweets are aggregated and summarized with relevance filtering (KEY POINTS + SIGNAL format) for agent consumption

## API Strategy

### Watchlist-Based Approach

The system uses a **Watchlist Registry** to maintain curated social media accounts and tracks tweets from these specific accounts about each game using the X API.

**Account Types**:
- **Team Accounts**: Official team Twitter accounts (one per team)
- **Beat Reporters**: Team-specific reporters who break injury/lineup news
- **Betting Analysts**: League-wide betting/analytics accounts

**Registry Structure**:
- `NBAWatchlistRegistry` / `NFLWatchlistRegistry`: Sport-specific registries containing all accounts
- `GameWatchlist`: Per-game filtered list (includes both teams' accounts + analysts)

**Search Strategy**:
For each account in the game watchlist, the system uses X API to search for that account's recent tweets:

```python
# For team accounts and beat reporters: search all recent tweets
query = f'from:{username}'

# For betting/analytics accounts: add team keywords to filter relevant tweets
query = f'from:{username} ("{home_team}" OR "{away_team}" OR "{home_tricode}" OR "{away_tricode}")'
```

**Usage**:
```python
from dojozero.data.socialmedia import NBAWatchlistRegistry, TwitterTopTweetsEvent
from dojozero.data._context import GameContext

# Build watchlist for a game
registry = NBAWatchlistRegistry()
watchlist = registry.build_game_watchlist("LAL", "GSW")
# Returns accounts for: LAL team, GSW team, LAL reporters, GSW reporters, all analysts

# Create event using X API
from dojozero.data.socialmedia._api import SocialMediaAPI

context = GameContext(
    sport="nba",
    home_team="Lakers",
    away_team="Warriors",
    home_tricode="LAL",
    away_tricode="GSW",
    game_date="2026-03-11",
    game_id="0022400608",
)

api = SocialMediaAPI()
event = await TwitterTopTweetsEvent.from_social_media(api=api, context=context)
# Aggregates tweets from all accounts in watchlist, then summarizes for agents
```

**X API Configuration**:
- Requires `DOJOZERO_X_API_BEARER_TOKEN` environment variable
- Uses `xdk` Python package
- Each account search has a 30-second timeout
- Fetches up to 10 tweets per account (first page only, no pagination)

## Data Format

### What Agents Receive

**TwitterTopTweetsEvent**:
- `summary`: **Primary content for agents** - Human-readable summary with relevance filtering (KEY POINTS + SIGNAL format)
- `tweets`: Raw list of tweets (for internal processing/debugging, **not sent to agents**)
  - Defaults to empty and is typically not populated on the final processed events emitted to consumers
  - Aggregated list of tweets from all tracked accounts (typically 10-30 tweets from ~8-12 accounts)
  - Each tweet contains: `text` (full content), `username`, `url`, `tweet_id`
- `query`: Search query description (e.g., "watchlist: 8 accounts (Lakers vs Warriors)")
- `source`: "twitter"
- `game_id`: ESPN game ID
- `sport`: Sport identifier ("nba", "nfl")

**Note**: Agents receive only the processed `summary` field via the `format_twitter_top_tweets` formatter. The raw `tweets` field is kept for internal processing and debugging but is not included in the formatted output sent to agents.

**Summary Format** (LLM-generated with relevance filtering):
- **KEY POINTS**: Categorized bullet points of main ideas (include time/date information when events occurred)
  - Categories: [BETTING], [GAME INFO], [INJURY], [LINEUP], [RESULT], [PERFORMANCE], [STRATEGY]
  - Example: `- [BETTING] Model loves Rockets against Nuggets tonight (2026-03-11)`
- **SIGNAL**: Decision-making insights based on KEY POINTS (include temporal context if relevant)
  - Example: `Model favors Rockets tonight despite Nuggets being home favorites. Multiple injury concerns...`

**Summarization Process**:
- Uses `summarize_content()` with relevance filtering to remove irrelevant content
- Timeout: 60 seconds (configurable via `summarize_timeout` parameter)
- If summarization fails or times out, `summary` field will be empty (but `tweets` are still available)

## Integration

- Triggered during `GameInitializeEvent` (pregame phase)
- Runs in parallel with other pregame data collection
- Publishes events to DataHub for agent consumption
- Event class: `TwitterTopTweetsEvent` (inherits from `PreGameInsightEvent`)

## Configuration

```yaml
    data_streams:
    # Pre-game insights: each event class owns search → typed event lifecycle
    - id: pre_game_insights_stream
      event_types:
      - twitter_top_tweets
```

**Watchlist Management**:
- Registry data is stored in code (`NBAWatchlistRegistry`, `NFLWatchlistRegistry`)
- Accounts can be updated by modifying the registry class attributes directly

