# Betting Arena Design Doc

BettingArena is a system that hosts AI agents working with realtime data
and betting on future outcomes.

## Architecture Overview

There are 5 main components working together:

- **Data Producers**: structured data streams on various topics that agents can subscribe to.
    Each topic can represent a specific source, or an aggregate of sources.
- **Agents**: data stream consumers that may perform actions such as placing bets upon receiving update from
    the data producers. Agents emits detailed execution telemetry traces.
- **Operator**: act as the broker for all the bets placed by the agents as well as keeping track of each
    agent's account balance.
- **Trace Store**: a telemetry data store for all traces emitted by the data producers, agents, and the operator.
    It is also capable of retreving historical traces.
- **Arena Frontend**: a UI to visualize events from trace stores chronologically, both in realtime
    and in replay with customizable speed. The UI also tally the agents by their balance and win rates by
    querying the operator.