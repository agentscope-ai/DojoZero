---
config:
  theme: redux
  layout: elk
---
flowchart TB
 subgraph APIs["External APIs"]
        NBAAPI1["NBADataAPI"]
        WebSearchAPI1["WebSearchAPI"]
        PolyAPI1["PolymarketAPI"]
        more1["..."]
  end
 subgraph NBADataStore["NBADataStore"]
        NBAAPI2["NBADataAPI"]
        Processor1["Processor"]
  end
 subgraph WebSearchStore["WebSearchStore"]
        WebSearchAPI2["WebSearchAPI"]
        Processor2["Processor"]
  end
 subgraph PolyDataStore["PolyDataStore"]
        PolyAPI2["PolymarketAPI"]
        Processor3["Processor"]
  end
 subgraph Stores["Data Stores<br>(manages/polls APIs, emit events)"]
        NBADataStore
        WebSearchStore
        PolyDataStore
        more2["..."]
  end
 subgraph DataHub["DataHub<br>(persistence, delivery)"]
        DataBus[("DataBus")]
        DataPersister["DataPersister"]
  end
 subgraph Agents["Agents"]
        Agent1["Aggressive Player"]
        Agent2["Zen Player"]
        Agent3["Balanced Player"]
  end
 subgraph MarketBroker["MarketBroker"]
        PolyAPI3["PolymarketAPI"]
  end
 subgraph Operators["Operators"]
        MarketBroker
  end
 subgraph Replay["Replay Coordinator"]
        ReplayFiles["replay files"]
  end
    NBADataStore -- events --> DataHub
    WebSearchStore -- events --> DataHub
    PolyDataStore -- events --> DataHub
    DataHub -- events --> Agents
    DataHub -- persistence --> Replay
    Replay -- replay events --> DataHub
    Operators -- serves --> Agents

    style DataBus fill:#d4edda
    style Agents fill:#ace4ee
    style Replay fill:#f8d7da
    style Operators fill:#fce4ec
    style APIs fill:#e1f5ff
    style Stores fill:#fff4e1