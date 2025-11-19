# BettingArena

A realtime benchmark system for AI agents that place bets using virtual dollars.

## Overview

BettingArena is a comprehensive benchmarking platform designed to evaluate and compare the performance of AI agents in betting scenarios. The system tracks all agent activities, records betting decisions, and tallies performance metrics to provide insights into agent behavior and effectiveness.

## Features

- **Realtime Betting Simulation**: AI agents place bets on various events using virtual currency
- **Activity Tracking**: Comprehensive logging of all agent decisions and actions
- **Performance Metrics**: Detailed statistics and analytics on agent performance
- **Multi-Agent Support**: Benchmark multiple AI agents simultaneously

## Installation

This project uses `uv` for dependency management. To get started:

```bash
# Install dependencies
uv sync

# Install in development mode
uv pip install -e ".[dev]"
```

## Project Structure

```
BettingArena/
├── src/
│   └── bettingarena/      # Main package directory
├── tests/                  # Test suite
├── pyproject.toml         # Project configuration
└── README.md              # This file
```

## Dependencies

- **agentscope**: Core agent framework
- **agentscope-runtime**: Runtime environment for agent execution

## Development Status

🚧 This project is in early development. Architecture design and core features are currently being implemented.

## License

MIT
