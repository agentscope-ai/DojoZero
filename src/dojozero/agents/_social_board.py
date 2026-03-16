"""Social Board for multi-agent communication.

This module provides a shared message board that allows agents to
communicate with each other during a trial. Agents can post messages
to share insights and read messages from other agents.

Key features:
- Post messages with character limit (200 chars)
- Read recent messages with configurable limit
- Digest function for memory compression injection
- Agent rate limiting (cooldown period between posts)
- Optional hot-topics list every N messages (LLM-generated, pushed as event)
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from dojozero.agents._toolkit import tool

logger = logging.getLogger(__name__)

# Maximum number of social messages to keep in memory per SocialBoard.
# Older messages are discarded when this cap is exceeded to avoid
# unbounded growth and excessive memory usage in long-running trials.
DEFAULT_MAX_SOCIAL_MESSAGES = 5000


class _CappedMessageList(list):
    """List subclass that caps the number of stored messages.
    Behaves like a regular list but automatically discards the oldest
    entries when the configured maximum size is exceeded.
    """

    def _prune(self) -> None:
        max_messages = DEFAULT_MAX_SOCIAL_MESSAGES
        if max_messages is None:
            return
        extra = len(self) - max_messages
        if extra > 0:
            # Remove the oldest messages to keep the most recent ones.
            del self[:extra]

    def append(self, item) -> None:  # type: ignore[override]
        super().append(item)
        self._prune()

    def extend(self, iterable) -> None:  # type: ignore[override]
        super().extend(iterable)
        self._prune()

    def insert(self, index, item) -> None:  # type: ignore[override]
        super().insert(index, item)
        self._prune()


@dataclass(slots=True, frozen=True)
class SocialMessage:
    """A message posted to the social board.

    Attributes:
        agent_id: ID of the agent who posted this message
        content: Message content (max 200 characters)
        timestamp: Unix timestamp when the message was posted
        trial_id: Trial ID this message belongs to
    """

    agent_id: str
    content: str
    timestamp: float
    trial_id: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "agent_id": self.agent_id,
            "content": self.content,
            "timestamp": self.timestamp,
            "trial_id": self.trial_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SocialMessage":
        """Create from dictionary."""
        return cls(
            agent_id=data["agent_id"],
            content=data["content"],
            timestamp=data["timestamp"],
            trial_id=data["trial_id"],
        )


@dataclass(slots=True, frozen=True)
class HotTopicsEvent:
    """Hot topics list generated from recent social board messages (LLM).

    Pushed as a StreamEvent to all agents every N messages.

    Attributes:
        trial_id: Trial ID
        topics: Ordered list of hot topic strings (e.g. ["topic1", "topic2", ...])
        generated_at: Unix timestamp when the list was generated
    """

    trial_id: str
    topics: tuple[str, ...]
    generated_at: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "trial_id": self.trial_id,
            "topics": list(self.topics),
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HotTopicsEvent":
        """Create from dictionary."""
        return cls(
            trial_id=data["trial_id"],
            topics=tuple(data.get("topics", [])),
            generated_at=data["generated_at"],
        )


def format_hot_topics_for_llm(event: HotTopicsEvent) -> str:
    """Format HotTopicsEvent for LLM consumption."""
    lines = ["[Social Board — Hot Topics]"]
    for i, topic in enumerate(event.topics, 1):
        lines.append(f"  {i}. {topic}")
    return "\n".join(lines)


@dataclass
class SocialBoard:
    """Shared message board for multi-agent communication.

    Features:
    - Thread-safe message posting and reading
    - Per-agent rate limiting (cooldown period)
    - Message character limit
    - Digest generation for memory compression

    Attributes:
        trial_id: Trial ID this board belongs to
        messages: List of all messages posted (in chronological order)
        max_message_chars: Maximum characters per message (default: 200)
        cooldown_rounds: Minimum rounds between posts per agent (default: 0).
            Rate limiting is implemented purely in terms of rounds, not time.
        agent_last_post_round: Track last post round for each agent
    """

    trial_id: str
    messages: list[SocialMessage] = field(default_factory=_CappedMessageList)
    max_message_chars: int = 200
    cooldown_rounds: int = 0
    agent_last_post_round: dict[str, int] = field(default_factory=dict)
    _lock: Any = field(default_factory=lambda: None, repr=False, init=False)

    def __post_init__(self) -> None:
        """Initialize lock for thread safety."""
        import threading

        self._lock = threading.Lock()

    def __getstate__(self) -> dict[str, Any]:
        """Return state for pickling, excluding the non-picklable lock.
        The lock is treated as transient and will be recreated in __setstate__.
        """
        # Support both regular and slots-based dataclasses
        if hasattr(self, "__dict__"):
            state: dict[str, Any] = self.__dict__.copy()
        else:
            # Fallback for slots; only include known attributes
            slots = getattr(self, "__slots__", ())
            state = {name: getattr(self, name) for name in slots if name != "_lock"}
        # Remove the non-picklable lock if present
        state.pop("_lock", None)
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore state after unpickling and recreate the lock."""
        import threading

        if hasattr(self, "__dict__"):
            self.__dict__.update(state)
        else:
            for name, value in state.items():
                setattr(self, name, value)
        # Recreate the transient lock
        self._lock = threading.Lock()

    def post_message(
        self, agent_id: str, content: str, current_round: int = 0
    ) -> tuple[bool, str]:
        """Post a message to the social board.

        Args:
            agent_id: ID of the agent posting
            content: Message content (will be truncated if too long)
            current_round: Current trial round number

        Returns:
            Tuple of (success, message)
            - (True, "") if successful
            - (False, error_reason) if failed (e.g., cooldown)
        """
        # Validate content
        if not content or not content.strip():
            return False, "Message content cannot be empty"

        # Check cooldown
        with self._lock:
            last_round = self.agent_last_post_round.get(agent_id, -self.cooldown_rounds)
            if current_round - last_round < self.cooldown_rounds:
                remaining = self.cooldown_rounds - (current_round - last_round)
                return (
                    False,
                    f"Cooldown active: {remaining} round(s) remaining",
                )

            # Truncate content if needed
            if len(content) > self.max_message_chars:
                content = content[: self.max_message_chars - 3] + "..."
                logger.info(
                    "Message from agent '%s' truncated to %d chars",
                    agent_id,
                    self.max_message_chars,
                )

            # Create and store message
            msg = SocialMessage(
                agent_id=agent_id,
                content=content.strip(),
                timestamp=time.time(),
                trial_id=self.trial_id,
            )
            self.messages.append(msg)
            self.agent_last_post_round[agent_id] = current_round

            logger.info(
                "Agent '%s' posted message to social board (round %d): %s",
                agent_id,
                current_round,
                content[:50] + "..." if len(content) > 50 else content,
            )
            return True, ""

    def read_messages(
        self,
        agent_id: str,
        limit: int = 10,
        exclude_own: bool = True,
    ) -> list[SocialMessage]:
        """Read recent messages from the social board.

        Args:
            agent_id: ID of the agent reading
            limit: Maximum number of messages to return
            exclude_own: Whether to exclude messages posted by this agent

        Returns:
            List of recent messages (most recent first)
        """
        with self._lock:
            messages = self.messages.copy()

        # Filter out own messages if requested
        if exclude_own:
            messages = [m for m in messages if m.agent_id != agent_id]

        # Return most recent first
        messages.reverse()
        return messages[:limit]

    def digest(self, agent_id: str, limit: int = 5) -> str:
        """Generate a digest of recent messages for memory injection.

        Called during _offload() to inject recent social messages
        into the compressed memory context.

        Args:
            agent_id: ID of the agent (to exclude own messages)
            limit: Number of recent messages to include

        Returns:
            Formatted digest string, or empty string if no messages
        """
        recent = self.read_messages(agent_id, limit=limit, exclude_own=True)

        if not recent:
            return ""

        lines = ["[Social Board Digest]"]
        for msg in recent:
            lines.append(f"- [{msg.agent_id}]: {msg.content}")

        return "\n".join(lines)

    def get_messages_count(self) -> int:
        """Get total number of messages posted."""
        with self._lock:
            return len(self.messages)

    def to_dict(self) -> dict[str, Any]:
        """Serialize board state for checkpointing."""
        with self._lock:
            return {
                "trial_id": self.trial_id,
                "messages": [m.to_dict() for m in self.messages],
                "agent_last_post_round": dict(self.agent_last_post_round),
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SocialBoard":
        """Deserialize board state from checkpoint."""
        import threading

        board = cls(
            trial_id=data["trial_id"],
            messages=[SocialMessage.from_dict(m) for m in data.get("messages", [])],
            agent_last_post_round=data.get("agent_last_post_round", {}),
        )
        board._lock = threading.Lock()
        return board


def create_social_board_tools(
    board: SocialBoard,
    *,
    hot_topics_interval: int = 20,
    hot_topics_trigger: Callable[[], Awaitable[None]] | None = None,
) -> list:
    """Create agent tool functions for social board operations.

    Args:
        board: SocialBoard instance to create tools for
        hot_topics_interval: Every N messages, fire hot_topics_trigger (default 20).
        hot_topics_trigger: Optional async callback to generate and push hot topics
            to all agents when message count reaches a multiple of hot_topics_interval.

    Returns:
        List of tool functions for toolkit registration.
        agent_id and current_round are injected by the agent's _bind_agent_id wrapper.
    """

    @tool
    async def post_message(
        agent_id: str,
        content: str,
        current_round: int = 0,
    ) -> str:
        """Post a message to the social board to share insights with other agents.

        Use this tool to share important insights with other agents.
        Character limit: 200.
        Args:
            content: Message content (max 200 characters)

        Returns:
            Success message or error reason
        """
        success, message = board.post_message(
            agent_id,
            content,
            current_round=current_round,
        )
        if success:
            if (
                hot_topics_trigger is not None
                and board.get_messages_count() % hot_topics_interval == 0
            ):
                await hot_topics_trigger()
            return "Message posted successfully"
        return f"Failed to post: {message}"

    @tool
    async def read_messages(agent_id: str, limit: int = 10) -> str:
        """Check what other agents are saying right now.

        Use this BEFORE making decisions - see their takes, spot opportunities, and stay in the loop.

        Args:
            limit: Number of messages to read (default: 10)

        Returns:
            Formatted list of recent messages
        """
        messages = board.read_messages(agent_id, limit=limit)

        if not messages:
            return "No messages on the social board"

        lines = ["[Social Board Messages]"]
        for msg in messages:
            lines.append(f"- [{msg.agent_id}]: {msg.content}")

        return "\n".join(lines)

    return [post_message, read_messages]
