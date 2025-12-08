"""ReplayCoordinator: Orchestrates replay from files to DataHub."""

from pathlib import Path
from typing import Any

from agentx.data._hub import DataHub


class ReplayCoordinator:
    """Orchestrates replay from files to DataHub for backtesting.
    
    Reads events from persistence files and replays them through DataHub
    to agents, simulating live data flow.
    """
    
    def __init__(self, data_hub: DataHub, replay_file: Path | str | None = None):
        """Initialize replay coordinator.
        
        Args:
            data_hub: DataHub instance to replay events to
            replay_file: Optional path to replay file (can be set later)
        """
        self.data_hub = data_hub
        self.replay_file = Path(replay_file) if replay_file else None
        self._replaying = False
    
    async def start_replay(self, replay_file: Path | str | None = None) -> None:
        """Start replay from a file.
        
        Args:
            replay_file: Path to replay file (uses instance replay_file if not provided)
        """
        if replay_file:
            self.replay_file = Path(replay_file)
        
        if not self.replay_file or not self.replay_file.exists():
            raise FileNotFoundError(f"Replay file not found: {self.replay_file}")
        
        self._replaying = True
        await self.data_hub.start_replay(str(self.replay_file))
    
    async def replay_all(self) -> None:
        """Replay all events from the file."""
        if not self._replaying:
            await self.start_replay()
        
        await self.data_hub.replay_all()
    
    async def replay_next(self) -> Any:
        """Replay next event.
        
        Returns:
            Next event or None if replay is complete
        """
        if not self._replaying:
            await self.start_replay()
        
        return await self.data_hub.replay_next()
    
    def stop_replay(self) -> None:
        """Stop replay."""
        self._replaying = False
        self.data_hub.stop_replay()

