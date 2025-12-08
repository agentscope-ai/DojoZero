1. Support Pull and Push Models
Agents can subscribe to push streams or trigger pull queries via operators

2. Support Replay
Data streams and queries can be replayed deterministically for backtesting. Use file baesd data stores for replay; should also support multi-store inter replay

3. Support data processing with LLM Integration
Allows data transformation with DataJuicer and LLMs

4. Separation of Concerns
Data API management should be separated from data persistence/caching/replay logic; data processor logic should be separate from steam or query infra logic and can be reused.

5. Use proper data models for all kinds of data inputs
Richer info makes agent building easier and context richer. And it's easier to strip awsay all the meta data if not needed later

6. Support data aggregation
Specifically for more generic scenarios; like NBA play by play aggregates to in game stats. The aggregators have to support both stateless and stateful to deal with different situations
