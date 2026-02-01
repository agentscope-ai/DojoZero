# Data Model Overhaul

**Date**: 2026-01-28
**Status**: Complete

## Motivation

The DojoZero data pipeline has grown organically, leading to:

1. **Fragmented type representations** -- the same concept (e.g. "team") is defined in 6+ places with different field sets and naming conventions.
2. **Data lost in translation** -- `GameInfo` from ESPN API carries rich data (venue, broadcast, odds, colors, logos) but `GameInitializeEvent` only passes team name strings. Arena server reconstructs team data from hardcoded lookup tables.
3. **No shared event hierarchy** -- NBA, NFL, and ESPN each define independent event types with no common base for equivalent concepts (play, game update, game result).
4. **Untyped nested data** -- events store `dict[str, Any]` for team stats, then provide wrapper properties to reconstruct typed objects.
5. **Inline dict construction** -- arena server builds API responses as hand-crafted dicts with camelCase keys and no type contract.

## Design

### Shared Models

All shared models live in `src/dojozero/data/_models.py` and are used across events, metadata, stores, and API responses.

```python
class PlayerIdentity(BaseModel):
    """Player identification for roster/lineup data."""
    player_id: str = ""
    name: str = ""
    position: str = ""          # "G", "F", "C"
    jersey: str = ""
    headshot_url: str = ""      # ESPN CDN: https://a.espncdn.com/i/headshots/{sport}/players/full/{id}.png

class TeamIdentity(BaseModel):
    """Single source of truth for team identification."""
    team_id: str = ""
    name: str = ""              # "Boston Celtics"
    tricode: str = ""           # "BOS"
    location: str = ""          # "Boston"
    color: str = ""             # Primary hex color
    alternate_color: str = ""
    logo_url: str = ""
    record: str = ""            # "42-18"
    players: list[PlayerIdentity] = []  # Full roster with headshots

class VenueInfo(BaseModel):
    """Venue/stadium information."""
    venue_id: str = ""
    name: str = ""
    city: str = ""
    state: str = ""
    indoor: bool = True

class MoneylineOdds(BaseModel):
    """Moneyline (match winner) market."""
    home_probability: float = 0.0
    away_probability: float = 0.0
    home_odds: float = 1.0          # Decimal odds
    away_odds: float = 1.0

class SpreadOdds(BaseModel):
    """Point spread market."""
    spread: float = 0.0
    home_probability: float = 0.0
    away_probability: float = 0.0

class OddsInfo(BaseModel):
    """All odds markets from a single provider."""
    provider: str = ""
    moneyline: MoneylineOdds | None = None
    spread: SpreadOdds | None = None
```

### Event Hierarchy

All events use Pydantic `BaseModel` with `frozen=True`. The hierarchy has two branches under `SportEvent`: `GameEvent` for game state and `PreGameInsightEvent` for supplementary intelligence.

```
DataEvent
    timestamp: datetime

    SportEvent
        game_id: str
        sport: str          # "nba", "nfl"

        GameEvent (game state changes)

            [Lifecycle]
            GameInitializeEvent
                home_team: TeamIdentity
                away_team: TeamIdentity
                venue: VenueInfo
                game_time: datetime
                broadcast: str
                odds: OddsInfo | None
                season_year: int
                season_type: str

            GameStartEvent
                home_starters: list[PlayerIdentity]
                away_starters: list[PlayerIdentity]

            GameResultEvent
                winner: str             # "home", "away", ""
                home_score: int
                away_score: int
                home_team_name: str
                away_team_name: str

            [Tier 1: Atomic -- single action as it happens]
            BasePlayEvent
                play_id: str
                sequence_number: int
                period: int
                clock: str
                description: str
                home_score: int
                away_score: int
                team_id: str
                team_tricode: str
                is_scoring_play: bool
                score_value: int

                NBAPlayEvent
                    action_type: str
                    player_name: str
                    player_id: int

                NFLPlayEvent
                    down: int
                    distance: int
                    yard_line: int
                    play_type: str
                    yards_gained: int
                    is_turnover: bool

            [Tier 2: Segment -- completed unit of play]
            BaseSegmentEvent
                segment_id: str
                segment_number: int
                team_id: str
                team_tricode: str
                start_period: int
                start_clock: str
                end_period: int
                end_clock: str
                plays_count: int
                result: str
                is_score: bool
                points_scored: int

                NFLDriveEvent
                    drive_id: str
                    drive_number: int
                    yards: int
                    time_elapsed: str

            [Tier 3: Snapshot -- current game state]
            BaseGameUpdateEvent
                period: int
                game_clock: str
                home_score: int
                away_score: int
                game_time_utc: str

                NBAGameUpdateEvent
                    home_team_stats: NBATeamGameStats
                    away_team_stats: NBATeamGameStats
                    player_stats: NBAGamePlayerStats

                NFLGameUpdateEvent
                    possession: str
                    down: int
                    distance: int
                    yard_line: str
                    home_team_stats: NFLTeamGameStats
                    away_team_stats: NFLTeamGameStats
                    home_line_scores: list[int]
                    away_line_scores: list[int]

            OddsUpdateEvent
                odds: OddsInfo

        PreGameInsightEvent (supplementary pre-game intelligence)
            source: str

            WebSearchInsightEvent (web search + LLM processing)
                query: str
                summary: str
                raw_results: list[dict]

                InjuryReportEvent
                    injured_players: dict[str, list[str]]

                PowerRankingEvent
                    rankings: dict[str, list[dict]]

                ExpertPredictionEvent
                    predictions: list[dict]

            StatsInsightEvent (ESPN stats API-derived)
                home_team_id: str
                away_team_id: str
                season_year: int
                season_type: str

                HeadToHeadEvent
                    total_games: int
                    home_wins: int
                    away_wins: int
                    last_n_games: int
                    games: list[dict]

                TeamStatsEvent
                    team_id: str
                    team_name: str
                    stats: dict[str, Any]
                    rank: dict[str, int]

                PlayerStatsEvent
                    team_id: str
                    team_name: str
                    players: list[dict]

                RecentFormEvent
                    team_id: str
                    team_name: str
                    last_n: int
                    wins: int
                    losses: int
                    streak: str
                    games: list[dict]
                    avg_points_scored: float
                    avg_points_allowed: float

            (future: SentimentEvent, NewsEvent, etc.)
```

WebSearchInsightEvent subclasses (`InjuryReportEvent`, `PowerRankingEvent`, `ExpertPredictionEvent`) own their full lifecycle via `WebSearchEventMixin.from_web_search()`: build query from `GameContext` → call search API → call LLM → return typed event.

Key design decisions:
- **Processors deleted**: The previous `StreamInitializer` → `WebSearchStore` → `BaseDashscopeProcessor` pipeline has been fully removed. LLM prompt/parsing logic lives on the event classes themselves via `WebSearchEventMixin`.
- **Subclass discovery**: Event classes are discovered at runtime via `WebSearchEventMixin.__subclasses__()` — no manual registry dict. Adding a new websearch event type means subclassing the mixin; datastreams and trial builders discover it automatically by matching the `event_type` Literal default against config suffixes.
- **`GameContext`**: A frozen dataclass (`websearch/_context.py`) carrying game-level fields (sport, teams, date) for search query template rendering. Constructed from trial metadata at build time and passed to event class `from_web_search()` calls.

### Three-Tier Model Rationale

| Tier | Name | What it captures | Granularity | Examples |
|------|------|-----------------|-------------|---------|
| 1 | **Atomic** | Single action as it happens | Individual play | Shot attempt, pass play, foul |
| 2 | **Segment** | Completed unit of play | Group of plays | NFL drive, basketball run |
| 3 | **Snapshot** | Current game state | Full state at a point in time | Boxscore update, scoreboard |

NBA needs Atomic + Snapshot. NFL needs all three (drives are a natural segment). Future sports slot in by implementing the tiers that make sense.

### Data Flow

```
ESPN API
  |
  v
Game Discovery --> GameInfo (with TeamIdentity, VenueInfo)
  |
  v
Trial Builder --> BettingTrialMetadata (carries TeamIdentity)
  |
  +--> trial.started span (team colors, logos, venue in tags)
  |
  +--> DataStore (receives TeamIdentity from metadata)
         |
         +--> GameInitializeEvent (full TeamIdentity + VenueInfo + OddsInfo)
         +--> NBAPlayEvent / NFLPlayEvent (atomic)
         +--> NFLDriveEvent (segment)
         +--> NBAGameUpdateEvent / NFLGameUpdateEvent (snapshot)
         +--> OddsUpdateEvent
         |
         v
       DataHub --> JSONL persistence + OTel Spans
                     |
                     v
                   Arena Server (reads typed models from spans)
                     |
                     v
                   Frontend (no hardcoded team lookups needed)
```

### Betting Domain Models

Betting models live in `src/dojozero/betting/_models.py`, extracted from the broker operator so consumers can import lightweight data contracts without the full broker implementation.

```
Enums
    EventStatus          SCHEDULED → LIVE → CLOSED → SETTLED
    OrderType            MARKET | LIMIT
    BettingPhase         PRE_GAME | IN_GAME
    BetStatus            PENDING → ACTIVE → SETTLED | CANCELLED
    BetOutcome           WIN | LOSS
    BetType              MONEYLINE | SPREAD | TOTAL

Account (Pydantic)
    agent_id, balance, created_at, last_updated

BettingEvent (Pydantic)
    event_id, home_team, away_team, game_time, status
    home_odds, away_odds, spread_lines, total_lines

BetRequest = BetRequestMoneyline | BetRequestSpread | BetRequestTotal
    (dataclasses with amount, selection, event_id, order_type, phase)

Bet (Pydantic)
    bet_id, agent_id, event_id, amount, selection, odds
    order_type, betting_phase, bet_type, status, outcome, payout

BetExecutedPayload / BetSettledPayload (dataclasses)
    Lightweight payloads for broker → span emission

Statistics (Pydantic)
    total_bets, total_wagered, wins, losses, win_rate, net_profit, roi
```

### Agent & Display Models

Display and API contract models live in `src/dojozero/core/_models.py`, shared between agents (for assembling span data) and the arena server (for serving typed JSON).

```
AgentInfo               id, name, avatar, color, model
AgentAction             id, agent: AgentInfo, action, time, timestamp
LeaderboardEntry        agent, winnings, winRate, totalBets, roi, rank
```

### Span Deserialization Models

Raw `SpanData` from the tracing layer (SLS/Jaeger) is deserialized into typed models in `src/dojozero/core/_models.py`:

```
SpanModel = Union[TrialLifecycleSpan, AgentMessageSpan, BrokerStateSpan,
                  ActorRegistrationSpan, BettingResultSpan, DataEvent]

deserialize_span(span) dispatches by operation_name:
    trial.started/stopped/terminated  →  TrialLifecycleSpan
    agent.input/response/tool_result  →  AgentMessageSpan
    broker.state_update               →  BrokerStateSpan
    *.registered                      →  ActorRegistrationSpan
    *result* / *payout*               →  BettingResultSpan
    event.*                           →  DataEvent (via Pydantic discriminated union)
```

### Arena Server Response Models

The arena server assembles presentation-layer view models from deserialized spans, static lookups, and computed aggregations. These are **not** SLS span types — they are cross-trial, frontend-facing models.

Arena models live in `src/dojozero/arena_server/_models.py`:

```
API Response Models
    StatsResponse           gamesPlayed, liveNow, wageredToday
    GameCardData            id, league, homeTeam, awayTeam, scores, status, date,
                            quarter, clock (live), winner, winAmount (completed)
    GamesResponse           liveGames, upcomingGames, completedGames
    LandingResponse         stats, liveGames, allGames, liveAgentActions
    LeaderboardResponse     leaderboard: list[LeaderboardEntry]
    AgentActionsResponse    actions: list[AgentAction]
    TrialListItem           id, phase, metadata
    TrialDetailResponse     trial_id, items

WebSocket Message Models
    WSSpanMessage           type="span", trial_id, timestamp, category, data
    WSTrialEndedMessage     type="trial_ended", trial_id, timestamp
    WSSnapshotMessage       type="snapshot", trial_id, timestamp, data
    WSHeartbeatMessage      type="heartbeat", timestamp
```

`TeamIdentity` uses `serialization_alias` so `model_dump(by_alias=True)` produces camelCase keys (`teamId`, `abbrev`, `city`, `alternateColor`, `logoUrl`) matching the frontend contract. The arena server's static team lookup tables (`_NBA_TEAMS`, `_NFL_TEAMS`) return `TeamIdentity` directly — no intermediate dict conversion.

### ESPN API Integration Details

**`$ref` URL resolution**: The ESPN Core API (`sports.core.api.espn.com`) returns `$ref` links instead of inline data for team and athlete objects in play-by-play responses: `{"$ref": ".../teams/24?lang=en&region=us"}` rather than `{"id": "24"}`. The `_id_from_ref()` helper in `nba/_api.py` extracts numeric IDs from these URLs so play-by-play events carry correct `team_id` and `player_id` values.

**PBP enrichment from boxscore**: Even after `$ref` resolution, the Core API PBP only provides numeric IDs without team tricodes or player names. The `GameStateTracker` maintains `_team_tricode_lookup` and `_player_name_lookup` maps populated from boxscore data. When PBP actions arrive with only IDs, the store enriches them with tricodes and names from these maps. Boxscore is always polled before PBP to ensure lookups are populated.

**Pre-game roster enrichment**: ESPN summary boxscores include team info (name, tricode, record) but no player data before tip-off. `NBAStore._enrich_boxscore_rosters()` detects missing player lists and fetches from the ESPN `team_roster` endpoint, injecting player dicts into the boxscore so `GameInitializeEvent` carries full rosters with headshot URLs.

**Starter detection**: ESPN boxscore player entries include `starter: true/false` once the game is in progress. Starters are extracted from boxscore data and cached in the `GameStateTracker`. When PBP data first arrives (game start signal), the `GameStartEvent` is emitted with `home_starters` and `away_starters` as `PlayerIdentity` lists. Pre-game, starters are unknown and these lists are empty.

**Headshot URL pattern**: `https://a.espncdn.com/i/headshots/{sport}/players/full/{player_id}.png` -- works for both NBA and NFL. Constructed at `PlayerIdentity` build time from the ESPN player ID.
