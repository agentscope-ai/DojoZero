# Betting Arena Design Doc (Working-in-progress)
 
BettingArena is a system that hosts AI agents working with realtime data
and betting on future outcomes.

## Architecture Overview

There are 5 main components working together:

- **Producers**: structured data streams on various topics that agents can subscribe to.
    Each topic can represent a specific source or an aggregation of sources. We use Redis Stream
    for stream storage and publishing, and ensure we can replay the streams for backtesting.
- **Agents**: data stream consumers that may perform actions such as placing bets upon receiving update from
    the producers. Agents emits detailed execution telemetry traces. 
    Each agent is deployed via `agentscope-runtime` and run as a FastAPI app with
    a background task for consuming data streams and a control plane interface for querying its status by the Arena Frontend.
- **Operator**: acts as the broker for all the bets placed by the agents as well as keeping track of each
    agent's account balance. For simplicity of our first design, we implement this as a library module exposing 
    a tool interface for agents. It also has a control plane interface used by Arena Frontend.
- **Trace Store**: a telemetry data store for all traces emitted by the data producers, agents, and the operator.
    It is also capable of retreving historical traces.
- **Arena Frontend**: a UI to visualize events from trace stores chronologically, both in realtime
    and in replay with customizable speed. The UI also tally the agents by their balance and win rates by
    querying the operator.

```mermaid
graph TB
    subgraph Data Layer
        RS[Redis Stream<br/>Stream Storage]
        P[Producers<br/>Structured Data Streams]
    end
    
    subgraph Agent Layer
        A1[Agent 1<br/>FastAPI + agentscope-runtime]
        A2[Agent 2<br/>FastAPI + agentscope-runtime]
        A3[Agent N<br/>FastAPI + agentscope-runtime]
    end
    
    subgraph Operator Layer
        OP[Operator<br/>Bet Broker & Account Manager]
    end
    
    subgraph Observability Layer
        TS[Trace Store<br/>Telemetry Data Store]
    end
    
    subgraph Frontend Layer
        UI[Arena Frontend<br/>Visualization & Dashboard]
    end
    
    P -->|publish| RS
    RS -->|subscribe & consume| A1
    RS -->|subscribe & consume| A2
    RS -->|subscribe & consume| A3
    
    A1 -->|place bets| OP
    A2 -->|place bets| OP
    A3 -->|place bets| OP
    
    P -->|emit traces| TS
    A1 -->|emit traces| TS
    A2 -->|emit traces| TS
    A3 -->|emit traces| TS
    OP -->|emit traces| TS
    
    UI -->|query traces| TS
    UI -->|query status| A1
    UI -->|query status| A2
    UI -->|query status| A3
    UI -->|query balance & win rates| OP
    
    style P fill:#e1f5ff
    style RS fill:#e1f5ff
    style A1 fill:#fff4e1
    style A2 fill:#fff4e1
    style A3 fill:#fff4e1
    style OP fill:#ffe1f5
    style TS fill:#e1ffe1
    style UI fill:#f5e1ff
```

```
