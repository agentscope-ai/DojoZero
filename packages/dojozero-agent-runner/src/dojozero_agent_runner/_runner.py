"""Runner core loop.

Connects to a DojoZero gateway as an external agent over AgentID Bearer
auth, builds an agentscope ReActAgent for the configured persona × LLM,
and reacts to incoming game events until the trial ends.

Intentionally lighter than the in-process ``BettingAgent`` — no event
throttling, retry queues, or memory compression. The canary's job is to
prove the externalization contract works end-to-end; performance
optimizations land only when a success-criteria gate fails without them.
"""

from __future__ import annotations

import asyncio
import logging

from agent_id_client_sdk import Client, Identity
from agentscope.agent import ReActAgent
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.tool import Toolkit
from dojozero_client import DojoClient, TrialConnection

from dojozero_agent_runner._config import RunnerConfig
from dojozero_agent_runner._emitting_model import EmittingChatModel
from dojozero_agent_runner._llm import create_formatter, create_model
from dojozero_agent_runner._tools import build_tools

logger = logging.getLogger(__name__)


async def run(config: RunnerConfig) -> None:
    """Main entry: connect, react to events, exit on trial_ended."""
    # Identity precedence: pre-loaded on config (programmatic / --portal-zip
    # callers), else read AGENTID_* env (k8s pod default).
    identity = config.identity if config.identity is not None else Identity.from_env()
    audience = config.agentid_audience or config.gateway_url
    agentid_client = Client(identity, default_audience=audience)

    logger.info(
        "Runner starting: name=%s trial=%s gateway=%s agent=%s",
        config.agent_display_name,
        config.trial_id,
        config.gateway_url,
        identity.agent_id,
    )

    dojo = DojoClient()
    async with dojo.connect_trial(
        gateway_url=config.gateway_url,
        # The gateway uses ``api_key`` to attach agent identity metadata.
        # We pass the verified AgentID ``sub`` so the registered agent_id
        # matches whatever the Bearer-authenticated requests will resolve to.
        api_key=identity.agent_id,
        agentid_client=agentid_client,
        agentid_audience=audience,
    ) as connection:
        logger.info(
            "Connected to trial %s as %s",
            connection.trial_id,
            connection.agent_id,
        )
        try:
            await _run_react_loop(config, connection)
        finally:
            logger.info("Runner exiting for trial %s", connection.trial_id)


async def _run_react_loop(config: RunnerConfig, connection: TrialConnection) -> None:
    """Build the ReActAgent and pump polled events through it until trial_ended."""
    model_type = str(config.llm_config.get("model_type", ""))
    # Wrap the chat model so each LLM call reports back to the gateway as
    # a Tier-1 ``model.call`` (best-effort; never blocks the loop).
    model = EmittingChatModel(create_model(config.llm_config), connection)
    formatter = create_formatter(model_type)

    toolkit = Toolkit()
    for tool in build_tools(connection):
        toolkit.register_tool_function(tool)

    agent = ReActAgent(
        name=config.agent_display_name,
        sys_prompt=config.sys_prompt,
        model=model,
        formatter=formatter,
        toolkit=toolkit,
        memory=InMemoryMemory(),
    )

    while True:
        result = await connection.poll_events(
            since=connection.last_sequence,
            limit=50,
        )
        if result.events:
            for event in result.events:
                await _handle_event(agent, event)

        if result.trial_ended is not None:
            # PollResult.trial_ended is a dict from the gateway response.
            final_balance = result.trial_ended.get("finalBalance", "n/a")
            logger.info(
                "trial_ended received (final_balance=%s); exiting loop",
                final_balance,
            )
            return

        await asyncio.sleep(config.poll_interval_seconds)


async def _handle_event(agent: ReActAgent, event) -> None:
    """Format one event as a user message and let the ReActAgent respond.

    Errors are logged and swallowed so a single misbehaving event doesn't
    take down the loop — the canary should keep running even when the LLM
    returns nonsense or a tool raises.
    """
    try:
        text = _format_event(event)
        msg = Msg(name="game", role="user", content=text)
        await agent.reply(msg)
    except Exception:  # noqa: BLE001 — never let one event kill the loop
        logger.exception("event handler failed; continuing")


def _format_event(event) -> str:
    """Render an event envelope as a short user-message body for the LLM.

    Kept terse on purpose: personas have their own response styles in the
    sys_prompt, and longer event descriptions just inflate the token bill
    without changing decisions.
    """
    payload = getattr(event, "payload", None) or {}
    parts = [
        f"event_type={getattr(event, 'event_type', 'unknown')}",
        f"sequence={getattr(event, 'sequence', '?')}",
    ]
    if payload:
        parts.append(f"payload={payload}")
    return " | ".join(parts)


__all__ = ["run"]
