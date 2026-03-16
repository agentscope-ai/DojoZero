"""Tests for SocialBoard posting, reading, digest, and hot-topics behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dojozero.agents._social_board import (
    SocialBoard,
    create_social_board_tools,
)


class TestSocialBoardPost:
    def test_post_message_success_and_truncation(self) -> None:
        board = SocialBoard(trial_id="t1", max_message_chars=10)

        # Normal message
        ok, msg = board.post_message("agent1", "hello", current_round=0)
        assert ok is True
        assert msg == ""
        assert len(board.messages) == 1
        assert board.messages[0].content == "hello"

        # Overlong message should be truncated and still succeed
        long_content = "abcdefghijk"  # 11 chars
        ok, msg = board.post_message("agent1", long_content, current_round=10)
        assert ok is True
        assert msg == ""
        assert len(board.messages) == 2
        # max_message_chars=10 => keep 7 chars + "..."
        assert board.messages[1].content == "abcdefg..."

    def test_post_message_rejects_empty_content(self) -> None:
        board = SocialBoard(trial_id="t1")

        ok, msg = board.post_message("agent1", "   ", current_round=0)
        assert ok is False
        assert "cannot be empty" in msg


class TestSocialBoardRead:
    def test_read_excludes_own_messages_by_default(self) -> None:
        board = SocialBoard(trial_id="t1")
        board.post_message("agent1", "msg1", current_round=0)
        board.post_message("agent2", "msg2", current_round=0)

        # By default, exclude_own=True
        msgs = board.read_messages("agent1", limit=10)
        # Should not see own message
        assert all(m.agent_id != "agent1" for m in msgs)
        # Should see other agent's message
        assert any(m.agent_id == "agent2" for m in msgs)

    def test_read_can_include_own_messages(self) -> None:
        board = SocialBoard(trial_id="t1")
        board.post_message("agent1", "msg1", current_round=0)
        board.post_message("agent2", "msg2", current_round=0)

        msgs_all = board.read_messages("agent1", limit=10, exclude_own=False)
        assert any(m.agent_id == "agent1" for m in msgs_all)
        assert any(m.agent_id == "agent2" for m in msgs_all)


class TestSocialBoardCooldown:
    def test_cooldown_rounds_respected(self) -> None:
        board = SocialBoard(trial_id="t1", cooldown_rounds=2)

        ok, msg = board.post_message("agent1", "first", current_round=0)
        assert ok is True
        assert msg == ""

        # current_round=1, distance=1 < 2 => should be blocked
        ok, msg = board.post_message("agent1", "second", current_round=1)
        assert ok is False
        assert "Cooldown active" in msg

        # current_round=2, distance=2 => allowed
        ok, msg = board.post_message("agent1", "third", current_round=2)
        assert ok is True
        assert msg == ""


class TestSocialBoardDigest:
    def test_digest_format_and_excludes_self(self) -> None:
        board = SocialBoard(trial_id="t1")
        board.post_message("agent1", "msg1", current_round=0)
        board.post_message("agent2", "msg2", current_round=0)

        digest = board.digest(agent_id="agent1", limit=5)

        assert digest
        assert digest.startswith("[Social Board Digest]")
        # Should not include own content
        assert "msg1" not in digest
        # Should include other agent's content
        assert "msg2" in digest
        assert "- [agent2]:" in digest

    def test_digest_empty_when_no_messages(self) -> None:
        board = SocialBoard(trial_id="t1")

        digest = board.digest(agent_id="agent1", limit=5)
        assert digest == ""


class TestHotTopicsTrigger:
    @pytest.mark.asyncio
    async def test_hot_topics_trigger_every_n_messages(self) -> None:
        board = SocialBoard(trial_id="t1")
        trigger = AsyncMock()

        tools = create_social_board_tools(
            board,
            hot_topics_interval=3,
            hot_topics_trigger=trigger,
        )
        # tools[0] is post_message tool
        post_message_tool = tools[0]

        # First 3 messages => trigger once
        for i in range(3):
            response = await post_message_tool(
                agent_id="agent1",
                content=f"m{i}",
                current_round=i,
            )
            # Tool functions return ToolResponse; content entries are dicts with "text"
            assert response.content
            first_block = response.content[0]
            assert isinstance(first_block, dict)
            assert "text" in first_block
            assert "Message posted successfully" in first_block["text"]

        assert trigger.await_count == 1

        # Next 3 messages => trigger second time (total 6 messages)
        for i in range(3, 6):
            await post_message_tool(
                agent_id="agent1",
                content=f"m{i}",
                current_round=i,
            )

        assert trigger.await_count == 2
