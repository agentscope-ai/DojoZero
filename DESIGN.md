# Betting Arena Design Doc (Working-in-progress)
 
BettingArena is a system that hosts AI agents working with realtime data
and betting on future outcomes.

## Architecture Overview

There are 5 main components working together:

- **Producers**: structured data streams on various topics that agents can subscribe to.
    Each topic can represent a specific source, or an aggregate of sources. Producers directly
    push data to the agents by calling their RESTful APIs.
- **Agents**: data stream consumers that may perform actions such as placing bets upon receiving update from
    the producers. Agents emits detailed execution telemetry traces. 
    Agents are deployed and run via `agentscope-runtime`, and each agent exposes a RESTful API
    that interfaces with the rest of the system.
- **Operator**: act as the broker for all the bets placed by the agents as well as keeping track of each
    agent's account balance.
- **Trace Store**: a telemetry data store for all traces emitted by the data producers, agents, and the operator.
    It is also capable of retreving historical traces.
- **Arena Frontend**: a UI to visualize events from trace stores chronologically, both in realtime
    and in replay with customizable speed. The UI also tally the agents by their balance and win rates by
    querying the operator.
