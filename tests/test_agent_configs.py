"""
Integration tests for agent configs.

Validates that all agent configurations in agents/ directory
are valid and their model endpoints work correctly.
"""

import os
from functools import lru_cache
from pathlib import Path

import pytest

from dojozero.agents import (
    load_agent_config,
    create_model,
    create_formatter,
    expand_agent_config,
    LLMConfig,
    AgentConfig,
)
from dojozero.betting._agent import BettingAgent
from dojozero.core import StreamEvent

# Test environment variable names
TEST_API_KEY_ENV = "DOJOZERO_OPENAI_API_KEY"
TEST_BASE_URL_ENV = "DOJOZERO_OPENAI_BASE_URL"

# Path to agent configs
CONFIG_DIR = Path(__file__).parent.parent / "agents"


def get_all_agent_config_files() -> list[Path]:
    """Get all YAML config files in the agents directory."""
    return list(CONFIG_DIR.glob("*.yaml"))


@lru_cache(maxsize=None)
def _load_agent_config_cached(config_path: Path) -> AgentConfig:
    """Load and cache agent configuration from a YAML file.

    Uses lru_cache to avoid redundant file reads during test collection
    and execution.
    """
    return load_agent_config(config_path)


def get_all_model_configs() -> list[tuple[str, LLMConfig]]:
    """Get all (config_name, llm_config) pairs from all agent configs.

    Returns a list of tuples containing the config file name and
    each individual LLM configuration from that file.
    """
    configs: list[tuple[str, LLMConfig]] = []
    for config_path in get_all_agent_config_files():
        agent_config = _load_agent_config_cached(config_path)
        expanded = expand_agent_config(agent_config)
        for single_config in expanded:
            configs.append((config_path.stem, single_config["llm"]))
    return configs


def _model_config_id(param: object) -> str:
    """Generate a test ID for model config parameters."""
    if isinstance(param, tuple) and len(param) == 2:
        config_name, llm_config = param
        model_name = (
            llm_config.get("model_name", "unknown")
            if isinstance(llm_config, dict)
            else "unknown"
        )
        return f"{config_name}-{model_name}"
    return str(param)


# =============================================================================
# Unit Tests - Config Loading and Validation
# =============================================================================


class TestAgentConfigLoading:
    """Test that all agent configs can be loaded and parsed correctly."""

    @pytest.mark.parametrize(
        "config_path",
        get_all_agent_config_files(),
        ids=lambda p: p.stem,
    )
    def test_config_loads_successfully(self, config_path: Path):
        """Test that each config file loads without errors."""
        config = _load_agent_config_cached(config_path)

        assert "name" in config, f"Config {config_path} missing 'name' field"
        assert "sys_prompt" in config, (
            f"Config {config_path} missing 'sys_prompt' field"
        )
        assert "llm" in config, f"Config {config_path} missing 'llm' field"
        assert isinstance(config["llm"], list), (
            f"Config {config_path} 'llm' should be a list"
        )
        assert len(config["llm"]) > 0, (
            f"Config {config_path} should have at least one LLM config"
        )

    @pytest.mark.parametrize(
        "config_path",
        get_all_agent_config_files(),
        ids=lambda p: p.stem,
    )
    def test_config_expands_correctly(self, config_path: Path):
        """Test that each config expands to separate model configs."""
        config = _load_agent_config_cached(config_path)
        expanded = expand_agent_config(config)

        assert len(expanded) == len(config["llm"]), (
            f"Config {config_path} expansion should create one config per model"
        )

        for single_config in expanded:
            assert "name" in single_config
            assert "sys_prompt" in single_config
            assert "llm" in single_config
            assert isinstance(single_config["llm"], dict), (
                "Expanded config 'llm' should be a single dict"
            )

    @pytest.mark.parametrize(
        "config_path",
        get_all_agent_config_files(),
        ids=lambda p: p.stem,
    )
    def test_llm_configs_have_required_fields(self, config_path: Path):
        """Test that each LLM config has required fields."""
        config = _load_agent_config_cached(config_path)

        for i, llm_config in enumerate(config["llm"]):
            assert "model_type" in llm_config, (
                f"Config {config_path} LLM[{i}] missing 'model_type'"
            )
            assert "model_name" in llm_config, (
                f"Config {config_path} LLM[{i}] missing 'model_name'"
            )
            assert llm_config["model_type"] in ("openai", "dashscope"), (
                f"Config {config_path} LLM[{i}] has unknown model_type: {llm_config['model_type']}"
            )


class TestModelCreation:
    """Test that models can be created from configs."""

    @pytest.mark.parametrize(
        "config_name,llm_config",
        get_all_model_configs(),
        ids=[_model_config_id(p) for p in get_all_model_configs()],
    )
    @pytest.mark.skipif(
        not os.environ.get(TEST_API_KEY_ENV),
        reason=f"{TEST_API_KEY_ENV} not set",
    )
    def test_model_can_be_created(self, config_name: str, llm_config: LLMConfig):
        """Test that each model can be instantiated without errors."""
        # Override env vars for testing
        llm_config = llm_config.copy()
        llm_config["api_key_env"] = TEST_API_KEY_ENV
        llm_config["base_url_env"] = TEST_BASE_URL_ENV

        model = create_model(llm_config)

        assert model is not None
        assert hasattr(model, "model_name")
        assert model.model_name == llm_config.get("model_name")


# =============================================================================
# Integration Tests - Model Endpoint Validation
# =============================================================================


def _create_test_agent(llm_config: LLMConfig, config_name: str) -> BettingAgent:
    """Create a BettingAgent for testing with the given LLM config."""
    llm_config = llm_config.copy()
    llm_config["api_key_env"] = TEST_API_KEY_ENV
    llm_config["base_url_env"] = TEST_BASE_URL_ENV
    model_type = llm_config.get("model_type", "openai")
    model_name = llm_config.get("model_name", "unknown")

    return BettingAgent(
        actor_id=f"test-{config_name}-{model_name}",
        trial_id="test-trial",
        name=f"test-{config_name}-{model_name}",
        sys_prompt="You are a test agent. Reply with 'OK' to any message.",
        model=create_model(llm_config),
        formatter=create_formatter(model_type),
    )


@pytest.mark.integration
class TestModelEndpoints:
    """Integration tests that validate model endpoints are working."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "config_name,llm_config",
        get_all_model_configs(),
        ids=[_model_config_id(p) for p in get_all_model_configs()],
    )
    @pytest.mark.skipif(
        not os.environ.get(TEST_API_KEY_ENV),
        reason=f"{TEST_API_KEY_ENV} not set",
    )
    async def test_model_endpoint_responds(
        self, config_name: str, llm_config: LLMConfig
    ):
        """Test that each model endpoint responds to a simple request.

        This test creates a BettingAgent and sends a simple event to verify
        the model endpoint is accessible and the API key is valid.
        """
        model_name = llm_config.get("model_name", "unknown")

        try:
            # Create a BettingAgent with this config
            agent = _create_test_agent(llm_config, config_name)
            await agent.start()

            # Send a simple test event
            test_event = StreamEvent(
                stream_id="test-stream",
                payload={"message": "Say 'hello' and nothing else."},
                sequence=0,
            )

            # Process the event - this will call the LLM
            await agent.handle_stream_event(test_event)

            # Check agent processed at least one event
            assert agent._event_count >= 1, (
                f"Agent should have processed at least 1 event, got {agent._event_count}"
            )

            # Verify we got an assistant response in memory
            assert len(agent._state) > 0, (
                "Agent memory should not be empty after processing event"
            )
            assistant_responses = [
                msg for msg in agent._state if msg.get("role") == "assistant"
            ]
            assert len(assistant_responses) > 0, (
                "Agent should have at least one assistant response in memory"
            )

            await agent.stop()
            response_preview = str(assistant_responses[-1].get("content", ""))[:50]
            print(f"\n✓ {config_name}/{model_name}: {response_preview}...")

        except Exception as e:
            pytest.fail(
                f"Model endpoint failed for {config_name}/{model_name}: {type(e).__name__}: {e}"
            )


@pytest.mark.integration
class TestAllConfigsEndToEnd:
    """End-to-end test that validates all configs work together."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not os.environ.get(TEST_API_KEY_ENV),
        reason=f"{TEST_API_KEY_ENV} not set",
    )
    async def test_all_configs_have_working_endpoints(self):
        """Aggregate test that reports on all model endpoint statuses.

        This test attempts to validate all model endpoints and provides
        a summary report of which endpoints are working vs failing.
        """
        results: dict[str, list[tuple[str, str]]] = {"passed": [], "failed": []}

        for config_path in get_all_agent_config_files():
            config = _load_agent_config_cached(config_path)
            expanded = expand_agent_config(config)

            for single_config in expanded:
                llm_config = single_config["llm"].copy()
                llm_config["api_key_env"] = TEST_API_KEY_ENV
                llm_config["base_url_env"] = TEST_BASE_URL_ENV

                model_name = llm_config.get("model_name", "unknown")
                test_id = f"{config_path.stem}/{model_name}"

                try:
                    # Create and test BettingAgent
                    agent = _create_test_agent(llm_config, config_path.stem)
                    await agent.start()

                    # Send a simple test event
                    test_event = StreamEvent(
                        stream_id="test-stream",
                        payload={"message": "Reply with 'ok'."},
                        sequence=0,
                    )
                    await agent.handle_stream_event(test_event)

                    # Check agent processed the event and got a response
                    if agent._event_count >= 1 and len(agent._state) > 0:
                        # Get the last assistant response from memory state
                        assistant_responses = [
                            msg
                            for msg in agent._state
                            if msg.get("role") == "assistant"
                        ]
                        if assistant_responses:
                            last_response = assistant_responses[-1].get("content", "")
                            # Truncate for display
                            response_preview = str(last_response)[:50]
                            results["passed"].append((test_id, response_preview))
                        else:
                            results["failed"].append(
                                (test_id, "No assistant response in memory")
                            )
                    else:
                        results["failed"].append(
                            (test_id, "No events processed or empty state")
                        )

                    await agent.stop()

                except Exception as e:
                    results["failed"].append(
                        (test_id, f"{type(e).__name__}: {str(e)[:100]}")
                    )

        # Print summary
        print("\n" + "=" * 60)
        print("MODEL ENDPOINT VALIDATION SUMMARY")
        print("=" * 60)

        print(f"\n✓ PASSED ({len(results['passed'])})")
        for test_id, response in results["passed"]:
            print(f"  - {test_id}: {response}...")

        if results["failed"]:
            print(f"\n✗ FAILED ({len(results['failed'])})")
            for test_id, error in results["failed"]:
                print(f"  - {test_id}: {error}")

            pytest.fail(
                f"{len(results['failed'])} model endpoint(s) failed. "
                f"See test output for details."
            )

        print("\n" + "=" * 60)
        print(f"All {len(results['passed'])} model endpoints validated successfully!")
        print("=" * 60)
