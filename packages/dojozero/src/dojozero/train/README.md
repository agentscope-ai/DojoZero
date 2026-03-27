# DojoZero RL Training Module

This module integrates DojoZero with AgentJet for reinforcement learning training of betting agents using GRPO (Group Relative Policy Optimization).

## Design Overview

### Architecture

The training module reuses DojoZero's existing `BettingAgent` and `BrokerOperator` while replacing the LLM backend with AgentJet's training infrastructure.

```
┌─────────────────────────────────────────────────────────────┐
│                     Episode Runner                          │
│  ┌─────────────────┐         ┌─────────────────────────┐   │
│  │  BettingAgent   │◄────────│  AgentJet Model Wrapper │   │
│  │  (ReActAgent)   │         │  (OpenAI-compatible API)│   │
│  └────────┬────────┘         └─────────────────────────┘   │
│           │ tool calls                                      │
│           ▼                                                 │
│  ┌─────────────────┐                                        │
│  │ BrokerOperator  │  (get_balance, place_bet, get_event)  │
│  └─────────────────┘                                        │
└─────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Full ReActAgent + Tool Calling**: Training maintains the same inference logic as production.
2. **Component Reuse**: `BettingAgent` and `BrokerOperator` are imported directly, not reimplemented.
3. **Event Routing**: 
   - `odds_update` events go only to broker (agent queries odds via `get_event` tool)
   - `game_result` events are used for settlement, not shown to agent
4. **Reward**: Based on ROI calculated from the final `odds_update` event.

### Module Structure

```
train/
├── __init__.py          # Module exports
├── model_adapter.py     # Adapts AgentJet API to agentscope model
├── event_filter.py      # Routes events to agent/broker
├── data_loader.py       # Loads game data from JSONL files
├── reward.py            # Calculates reward from betting outcomes
├── episode_runner.py    # Runs single episode with BettingAgent + BrokerOperator
├── agent_run.py         # Entry point for Swarm worker
├── agent_roll.py        # Training loop with SwarmClient
└── README.md            # This file
```

## Setup

### Prerequisites

1. DojoZero installed with dependencies
2. AgentJet framework available

### Link AgentJet

From the DojoZero root directory, create a symlink to AgentJet:

```bash
cd DojoZero
ln -sf ../AgentJet/ajet ajet
ls -la ajet  # Verify the symlink
```

## Running Training

### Terminal 1: Start Swarm Server

```bash
cd DojoZero
ajet-swarm start
```

### Terminal 2: Run Training Loop

```bash
cd DojoZero
python -m dojozero.train.agent_roll
```

### Optional: Monitor Training

```bash
python -m ajet.launcher --swarm-overwatch=http://localhost:10086
```

## Configuration

Key parameters in `agent_roll.py`:

| Parameter | Description |
|-----------|-------------|
| `LOCAL_GRPO_N` | GRPO group size (rollouts per task) |
| `REMOTE_BATCH_SIZE` | Training batch size |
| `REMOTE_ALLOCATE_GPU_PER_NODE` | Number of GPUs to use |
| `EVENT_FILTER_MODE` | Event compression: `full`, `scoring`, `sampled` |

## Event Filter Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `full` | All events | Short games, debugging |
| `scoring` | Only scoring plays | Default, reduces context length |
| `sampled` | Every Nth event | Moderate compression |

## Event Routing

| Event Type | To Agent | To Broker | Purpose |
|------------|----------|-----------|---------|
| `game_initialize` | Yes | Yes | Initialize game |
| `pregame_stats` | Yes | No | Pre-game analysis |
| `injury_report` | Yes | No | Injury information |
| `expert_prediction` | Yes | No | Expert picks |
| `game_start` | Yes | Yes | Game begins |
| `nba_play` | Yes (filtered) | No | Play-by-play |
| `nba_game_update` | Yes (filtered) | Yes | Score updates |
| `odds_update` | **No** | Yes | Agent uses `get_event` tool |
| `game_result` | **No** | Yes | Settlement |

## Troubleshooting

### Connection Refused

If you see `RuntimeError: Unable to connect to swarm server`:
- Ensure `ajet-swarm start` is running in another terminal
- Check if port 10086 is available

### Module Not Found

If AgentJet imports fail:
- Verify the symlink: `ls -la ajet`
- Recreate if needed: `ln -sf ../AgentJet/ajet ajet`
