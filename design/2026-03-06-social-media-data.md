# Social Media DataStream Design

## Design

1. **Account-Based Tracking**: Track specific curated accounts (team accounts, beat reporters, betting analysts)
2. **X API Integration**: Uses official X API (xdk) to search recent posts from watchlist accounts
3. **Summarization**: Tweets are grouped by account, each account's tweets are summarized separately with relevance filtering (KEY POINTS + SIGNAL format), then combined for agent consumption

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
The system uses tailored X API queries based on account type:
- **Betting/analytics accounts**: Filter by both team names and tricodes to find game-relevant content
- **Official team accounts**: Filter by opponent name/tricode plus game-relevant terms (injury, lineup, status, starting, tonight, gameday), excluding retweets
- **Beat reporters**: Exclude retweets and replies to capture only original reporting

**Usage**:
```python
from dojozero.data.socialmedia._api import SocialMediaAPI
from dojozero.data.socialmedia._events import TwitterTopTweetsEvent
from dojozero.data._context import GameContext

# Create GameContext with game information
context = GameContext(
    sport="nba",
    home_team="Lakers",
    away_team="Warriors",
    home_tricode="LAL",
    away_tricode="GSW",
    game_date="2026-03-11",
    game_id="0022400608",  # ESPN game ID
)

# Fetch and process tweets (watchlist is built automatically from context)
api = SocialMediaAPI()
event = await TwitterTopTweetsEvent.from_social_media(api=api, context=context)
# Fetches tweets from all accounts in watchlist, groups by account, summarizes each account separately, then combines summaries
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
- Each account's summary follows KEY POINTS + SIGNAL format, then combined with account prefix:
  ```
  [@{username}]
  KEY POINTS:
  - [CATEGORY] Concise summary sentence (YYYY-MM-DD)
  ...
  SIGNAL:
  Decision-making insights...
  ```
- **KEY POINTS**: Categorized bullet points of main ideas (include time/date information when events occurred)
  - Categories: [BETTING], [GAME INFO], [INJURY], [LINEUP], [RESULT], [PERFORMANCE], [STRATEGY]
  - Example: `- [BETTING] Model loves Rockets against Nuggets tonight (2026-03-11)`
- **SIGNAL**: Decision-making insights based on KEY POINTS (include temporal context if relevant)
  - Example: `Model favors Rockets tonight despite Nuggets being home favorites. Multiple injury concerns...`

**Summarization Process**:
- Tweets are grouped by account (username)
- Each account's tweets are summarized separately using `summarize_content()` with relevance filtering
- Accounts with no relevant content are filtered out
- Summaries from all relevant accounts are combined with `[@{username}]\n{account_summary}` format
- Summarization timeout: 60 seconds per account (configurable via `summarize_timeout` parameter)
- If summarization fails or times out for an account, that account's summary is excluded (but tweets are still available)

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

