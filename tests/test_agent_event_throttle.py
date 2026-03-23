"""Unit tests for BettingAgent event throttle (min_event_interval).

These tests verify the cooldown / batching behaviour added to
``handle_stream_event`` without hitting any real LLM.  The ReActAgent is
never invoked — we patch ``_process_events_with_retry`` so we can observe
*when* and *with how many events* the agent decides to call the model.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dojozero.core import StreamEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(min_event_interval: float = 5.0):
    """Create a BettingAgent with a mocked model/formatter and short throttle."""
    from agentscope.formatter import FormatterBase
    from agentscope.model import ChatModelBase

    from dojozero.betting._agent import BettingAgent

    mock_model = MagicMock(spec=ChatModelBase)
    mock_model.model_name = "mock-model"
    mock_formatter = MagicMock(spec=FormatterBase)

    agent = BettingAgent(
        actor_id="test-agent",
        trial_id="test-trial",
        name="test",
        sys_prompt="you are a test agent",
        model=mock_model,
        formatter=mock_formatter,
    )
    # Use a short interval for fast tests
    agent._min_event_interval = min_event_interval
    return agent


def _event(seq: int = 0) -> StreamEvent:
    return StreamEvent(
        stream_id="test-stream",
        payload={"data": f"event-{seq}"},
        sequence=seq,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_event_processed_immediately():
    """The very first event should not be throttled (no prior process time)."""
    agent = _make_agent(min_event_interval=600.0)  # very long cooldown

    with patch.object(
        agent, "_process_events_with_retry", new_callable=AsyncMock
    ) as mock_process:
        await agent.handle_stream_event(_event(0))

    mock_process.assert_awaited_once()
    events_arg = mock_process.call_args[0][0]
    assert len(events_arg) == 1


@pytest.mark.asyncio
async def test_second_event_throttled_during_cooldown():
    """An event arriving within the cooldown window should be queued, not
    processed immediately."""
    agent = _make_agent(min_event_interval=600.0)

    with patch.object(
        agent, "_process_events_with_retry", new_callable=AsyncMock
    ) as mock_process:
        # First event — processed immediately
        await agent.handle_stream_event(_event(0))
        assert mock_process.await_count == 1

        # Second event — should be queued (within cooldown)
        await agent.handle_stream_event(_event(1))
        # Still only 1 call — the second event was queued
        assert mock_process.await_count == 1

    # Event is sitting in the queue
    assert len(agent._event_queue) == 1


@pytest.mark.asyncio
async def test_cooldown_task_drains_queue():
    """After the cooldown expires, the scheduled task should drain the queue."""
    agent = _make_agent(min_event_interval=0.1)  # 100ms for fast test

    with patch.object(
        agent, "_process_events_with_retry", new_callable=AsyncMock
    ) as mock_process:
        await agent.handle_stream_event(_event(0))
        assert mock_process.await_count == 1

        # Queue two events during cooldown
        await agent.handle_stream_event(_event(1))
        await agent.handle_stream_event(_event(2))
        assert mock_process.await_count == 1

        # A cooldown task should have been scheduled
        assert agent._cooldown_task is not None

        # Wait for cooldown to fire
        await agent._cooldown_task

    # Second call should have batched both queued events
    assert mock_process.await_count == 2
    batched_events = mock_process.call_args_list[1][0][0]
    assert len(batched_events) == 2


@pytest.mark.asyncio
async def test_event_after_cooldown_expires_processed_immediately():
    """An event arriving after the cooldown has fully elapsed should be
    processed right away (no queuing)."""
    agent = _make_agent(min_event_interval=0.05)  # 50ms

    with patch.object(
        agent, "_process_events_with_retry", new_callable=AsyncMock
    ) as mock_process:
        await agent.handle_stream_event(_event(0))
        assert mock_process.await_count == 1

        # Wait for cooldown to fully expire
        await asyncio.sleep(0.1)

        await agent.handle_stream_event(_event(1))
        # Should be processed immediately — not queued
        assert mock_process.await_count == 2


@pytest.mark.asyncio
async def test_events_queued_while_processing():
    """Events arriving while the agent is actively processing should be
    queued and then processed in the next drain cycle."""
    agent = _make_agent(min_event_interval=0.0)  # no cooldown

    processing_started = asyncio.Event()
    processing_gate = asyncio.Event()

    original_process = AsyncMock()

    async def slow_process(events, retry_count=0):
        processing_started.set()
        await processing_gate.wait()
        return await original_process(events, retry_count=retry_count)

    with patch.object(agent, "_process_events_with_retry", side_effect=slow_process):
        # Start processing first event (will block on gate)
        task = asyncio.create_task(agent.handle_stream_event(_event(0)))
        await processing_started.wait()

        # While blocked, queue more events
        await agent.handle_stream_event(_event(1))
        await agent.handle_stream_event(_event(2))

        # Release the gate — first batch finishes, then queued batch runs
        processing_gate.set()
        await task

    # original_process was called twice: once for event 0, once for events 1+2
    assert original_process.await_count == 2
    second_batch = original_process.call_args_list[1][0][0]
    assert len(second_batch) == 2


@pytest.mark.asyncio
async def test_no_duplicate_cooldown_tasks():
    """Multiple events arriving during cooldown should not spawn multiple
    cooldown tasks — only one should be active."""
    agent = _make_agent(min_event_interval=600.0)

    with patch.object(agent, "_process_events_with_retry", new_callable=AsyncMock):
        await agent.handle_stream_event(_event(0))

        # Queue several events rapidly
        await agent.handle_stream_event(_event(1))
        first_task = agent._cooldown_task
        await agent.handle_stream_event(_event(2))
        await agent.handle_stream_event(_event(3))

    # Should still be the same task object (not recreated)
    assert agent._cooldown_task is first_task
    assert len(agent._event_queue) == 3

    # Cleanup: cancel the long-lived task
    if agent._cooldown_task and not agent._cooldown_task.done():
        agent._cooldown_task.cancel()
        try:
            await agent._cooldown_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_memory_token_threshold_default():
    """Verify the memory compression threshold is set to ~80% of 128K."""
    agent = _make_agent()
    assert agent._memory_token_threshold == 102400


@pytest.mark.asyncio
async def test_stop_cancels_cooldown_task():
    """Stopping the agent should cancel any pending cooldown task."""
    agent = _make_agent(min_event_interval=600.0)

    with patch.object(agent, "_process_events_with_retry", new_callable=AsyncMock):
        await agent.handle_stream_event(_event(0))
        await agent.handle_stream_event(_event(1))

    assert agent._cooldown_task is not None
    assert not agent._cooldown_task.done()

    await agent.stop()

    assert agent._cooldown_task is None
