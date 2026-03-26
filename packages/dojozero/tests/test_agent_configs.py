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
    load_llm_file_config,
    load_persona_config,
    create_model,
    create_formatter,
    expand_agent_config,
    filter_llm_configs_by_credentials,
    llm_config_has_credentials,
    LLMConfig,
    AgentConfig,
)
from dojozero.betting._agent import BettingAgent
from dojozero.core import StreamEvent

# Test environment variable names
TEST_API_KEY_ENV = "DOJOZERO_OPENAI_API_KEY"
TEST_BASE_URL_ENV = "DOJOZERO_OPENAI_BASE_URL"

# Path to agent configs
AGENTS_DIR = Path(__file__).parent.parent / "agents"
PERSONAS_DIR = AGENTS_DIR / "personas"
LLMS_DIR = AGENTS_DIR / "llms"

# Default LLM config for tests
DEFAULT_LLM_CONFIG_PATH = LLMS_DIR / "default.yaml"


def get_all_persona_files() -> list[Path]:
    """Get all YAML config files in the personas directory."""
    return list(PERSONAS_DIR.glob("*.yaml"))


def get_all_llm_files() -> list[Path]:
    """Get all YAML config files in the llms directory."""
    return list(LLMS_DIR.glob("*.yaml"))


@lru_cache(maxsize=None)
def _load_agent_config_cached(
    persona_config_path: Path,
    llm_config_path: Path,
) -> AgentConfig:
    """Load and cache agent configuration from YAML files.

    Uses lru_cache to avoid redundant file reads during test collection
    and execution.
    """
    return load_agent_config(persona_config_path, llm_config_path)


def get_all_model_configs() -> list[tuple[str, LLMConfig]]:
    """Get all (llm_file_name, llm_config) pairs from all LLM config files.

    Returns a list of tuples containing the LLM config file name and
    each individual LLM configuration from that file.
    """
    configs: list[tuple[str, LLMConfig]] = []
    for llm_path in get_all_llm_files():
        llm_file_config = load_llm_file_config(llm_path)
        for llm_config in llm_file_config["llm"]:
            configs.append((llm_path.stem, llm_config))
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


class TestPersonaConfigLoading:
    """Test that all persona configs can be loaded and parsed correctly."""

    @pytest.mark.parametrize(
        "persona_path",
        get_all_persona_files(),
        ids=lambda p: p.stem,
    )
    def test_persona_loads_successfully(self, persona_path: Path):
        """Test that each persona config file loads without errors."""
        persona_config = load_persona_config(persona_path)

        assert "sys_prompt" in persona_config, (
            f"Persona {persona_path} missing 'sys_prompt' field"
        )
        assert len(persona_config["sys_prompt"]) > 0, (
            f"Persona {persona_path} 'sys_prompt' should not be empty"
        )


class TestLLMCredentialFilter:
    """Credential-based filtering for load_agent_config."""

    def test_llm_config_has_credentials_requires_key(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        cfg: LLMConfig = {
            "model_type": "openai",
            "model_name": "gpt-4",
            "api_key_env": "DOJOZERO_OPENAI_API_KEY",
        }
        monkeypatch.delenv("DOJOZERO_OPENAI_API_KEY", raising=False)
        assert llm_config_has_credentials(cfg) is False
        monkeypatch.setenv("DOJOZERO_OPENAI_API_KEY", "sk-test")
        assert llm_config_has_credentials(cfg) is True

    def test_filter_llm_configs_by_credentials(self, monkeypatch: pytest.MonkeyPatch):
        cfgs: list[LLMConfig] = [
            {
                "model_type": "openai",
                "model_name": "a",
                "api_key_env": "DOJOZERO_OPENAI_API_KEY",
            },
            {
                "model_type": "anthropic",
                "model_name": "b",
                "api_key_env": "DOJOZERO_ANTHROPIC_API_KEY",
            },
        ]
        monkeypatch.setenv("DOJOZERO_OPENAI_API_KEY", "x")
        monkeypatch.delenv("DOJOZERO_ANTHROPIC_API_KEY", raising=False)
        out = filter_llm_configs_by_credentials(cfgs)
        assert len(out) == 1
        assert out[0].get("model_name") == "a"


class TestLLMConfigLoading:
    """Test that all LLM configs can be loaded and parsed correctly."""

    @pytest.mark.parametrize(
        "llm_path",
        get_all_llm_files(),
        ids=lambda p: p.stem,
    )
    def test_llm_config_loads_successfully(self, llm_path: Path):
        """Test that each LLM config file loads without errors."""
        llm_file_config = load_llm_file_config(llm_path)

        assert "llm" in llm_file_config, f"LLM config {llm_path} missing 'llm' field"
        assert isinstance(llm_file_config["llm"], list), (
            f"LLM config {llm_path} 'llm' should be a list"
        )
        assert len(llm_file_config["llm"]) > 0, (
            f"LLM config {llm_path} should have at least one LLM config"
        )

    @pytest.mark.parametrize(
        "llm_path",
        get_all_llm_files(),
        ids=lambda p: p.stem,
    )
    def test_llm_configs_have_required_fields(self, llm_path: Path):
        """Test that each LLM config has required fields."""
        llm_file_config = load_llm_file_config(llm_path)

        for i, llm_config in enumerate(llm_file_config["llm"]):
            assert "model_type" in llm_config, (
                f"LLM config {llm_path} LLM[{i}] missing 'model_type'"
            )
            assert "model_name" in llm_config, (
                f"LLM config {llm_path} LLM[{i}] missing 'model_name'"
            )
            assert llm_config["model_type"] in (
                "openai",
                "dashscope",
                "anthropic",
                "gemini",
                "grok",
            ), (
                f"LLM config {llm_path} LLM[{i}] has unknown model_type: {llm_config['model_type']}"
            )


class TestAgentConfigLoading:
    """Test that agent configs can be loaded by combining persona and LLM configs."""

    @pytest.mark.parametrize(
        "persona_path",
        get_all_persona_files(),
        ids=lambda p: p.stem,
    )
    def test_combined_config_loads_successfully(self, persona_path: Path):
        """Test that persona + default LLM config loads without errors."""
        config = _load_agent_config_cached(persona_path, DEFAULT_LLM_CONFIG_PATH)

        assert "sys_prompt" in config, "Config missing 'sys_prompt' field"
        assert "llm" in config, "Config missing 'llm' field"
        assert isinstance(config["llm"], list), "Config 'llm' should be a list"
        # Credential filtering may yield zero entries when no DOJOZERO_* keys are set
        for item in config["llm"]:
            assert "model_name" in item
            assert "model_type" in item

    @pytest.mark.parametrize(
        "persona_path",
        get_all_persona_files(),
        ids=lambda p: p.stem,
    )
    def test_config_expands_correctly(self, persona_path: Path):
        """Test that each config expands to separate model configs."""
        config = _load_agent_config_cached(persona_path, DEFAULT_LLM_CONFIG_PATH)
        expanded = expand_agent_config(config)

        assert len(expanded) == len(config["llm"]), (
            "Config expansion should create one config per model"
        )

        for single_config in expanded:
            assert "name" in single_config
            assert "sys_prompt" in single_config
            assert "llm" in single_config
            assert isinstance(single_config["llm"], dict), (
                "Expanded config 'llm' should be a dict"
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
        # Override env vars for testing - only set base_url_env if the env var exists
        test_config: LLMConfig = {**llm_config, "api_key_env": TEST_API_KEY_ENV}
        if os.environ.get(TEST_BASE_URL_ENV):
            test_config["base_url_env"] = TEST_BASE_URL_ENV
        else:
            test_config["base_url_env"] = None

        model = create_model(test_config)

        assert model is not None
        assert hasattr(model, "model_name")
        assert model.model_name == test_config.get("model_name")


# =============================================================================
# Integration Tests - Model Endpoint Validation
# =============================================================================


def _create_test_agent(llm_config: LLMConfig, config_name: str) -> BettingAgent:
    """Create a BettingAgent for testing with the given LLM config."""
    # Override env vars for testing - only set base_url_env if the env var exists
    test_config: LLMConfig = {**llm_config, "api_key_env": TEST_API_KEY_ENV}
    if os.environ.get(TEST_BASE_URL_ENV):
        test_config["base_url_env"] = TEST_BASE_URL_ENV
    else:
        test_config["base_url_env"] = None
    model_type = test_config.get("model_type", "openai")
    model_name = test_config.get("model_name", "unknown")

    return BettingAgent(
        actor_id=f"test-{config_name}-{model_name}",
        trial_id="test-trial",
        name=f"test-{config_name}-{model_name}",
        sys_prompt="You are a test agent. Reply with 'OK' to any message.",
        model=create_model(test_config),
        formatter=create_formatter(model_type, model_name),
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
        Tests each persona with each LLM config combination.
        """
        results: dict[str, list[tuple[str, str]]] = {"passed": [], "failed": []}

        for persona_path in get_all_persona_files():
            for llm_path in get_all_llm_files():
                config = _load_agent_config_cached(persona_path, llm_path)
                expanded = expand_agent_config(config)

                for single_config in expanded:
                    # Override env vars for testing - only set base_url_env if the env var exists
                    llm_config: LLMConfig = {
                        **single_config["llm"],
                        "api_key_env": TEST_API_KEY_ENV,
                    }
                    if os.environ.get(TEST_BASE_URL_ENV):
                        llm_config["base_url_env"] = TEST_BASE_URL_ENV
                    else:
                        llm_config["base_url_env"] = None

                    model_name = llm_config.get("model_name", "unknown")
                    test_id = f"{persona_path.stem}/{llm_path.stem}/{model_name}"

                    try:
                        # Create and test BettingAgent
                        agent = _create_test_agent(llm_config, persona_path.stem)
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
                                last_response = assistant_responses[-1].get(
                                    "content", ""
                                )
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
