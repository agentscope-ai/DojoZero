"""Pytest configuration and shared fixtures."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# Test Environment Configuration
# =============================================================================

# Test-specific environment variable names to avoid conflicts with other apps
TEST_API_KEY_ENV = "DOJOZERO_OPENAI_API_KEY"
TEST_BASE_URL_ENV = "DOJOZERO_OPENAI_BASE_URL"

# Common paths
AGENTS_DIR = Path(__file__).parent.parent / "agents"
PERSONAS_DIR = AGENTS_DIR / "personas"
LLMS_DIR = AGENTS_DIR / "llms"

# Default LLM config for tests
DEFAULT_LLM_CONFIG_PATH = LLMS_DIR / "qwen.yaml"

# Persona config paths
BASIC_PERSONA_PATH = PERSONAS_DIR / "basic.yaml"
WHALE_PERSONA_PATH = PERSONAS_DIR / "whale.yaml"
SHEEP_PERSONA_PATH = PERSONAS_DIR / "sheep.yaml"
SHARK_PERSONA_PATH = PERSONAS_DIR / "shark.yaml"


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that make real API calls",
    )


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (skipped by default)"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to skip integration tests by default."""
    if config.getoption("--run-integration"):
        # --run-integration given in cli: do not skip integration tests
        return

    skip_integration = pytest.mark.skip(reason="need --run-integration option to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


# =============================================================================
# Shared Fixtures for Moneyline Agent Tests
# =============================================================================


@pytest.fixture
def trial_id() -> str:
    """Default trial ID for tests."""
    return "test-trial"


@pytest.fixture
def test_game_id() -> str:
    """Default game ID for tests."""
    return "test_game_2024"


@pytest.fixture
def nba_game_init_data(test_game_id):
    """NBA game initialization data."""
    from datetime import datetime

    return {
        "game_id": test_game_id,
        "home_team": "Lakers",
        "away_team": "Warriors",
        "game_time": datetime.now(),
    }


@pytest.fixture
def nba_odds_data(test_game_id):
    """NBA odds update data."""
    return {
        "game_id": test_game_id,
        "home_odds": 1.85,
        "away_odds": 2.10,
    }


@pytest.fixture
def nfl_game_init_data(test_game_id):
    """NFL game initialization data."""
    from datetime import datetime

    return {
        "game_id": test_game_id,
        "home_team": "Baltimore Ravens",
        "away_team": "Kansas City Chiefs",
        "home_team_abbreviation": "BAL",
        "away_team_abbreviation": "KC",
        "venue": "M&T Bank Stadium",
        "game_time": datetime.now(),
        "week": 1,
    }


@pytest.fixture
def nfl_odds_data(test_game_id):
    """NFL odds update data."""
    return {
        "game_id": test_game_id,
        "provider": "polymarket",
        "moneyline_home": -150,
        "moneyline_away": 130,
        "spread": -3.0,
        "over_under": 47.5,
        "home_team": "Baltimore Ravens",
        "away_team": "Kansas City Chiefs",
    }


def create_broker_fixture(actor_id: str, trial_id: str = "test-trial"):
    """Factory function to create broker fixtures."""
    from dojozero.betting import BrokerOperator
    from dojozero.core import RuntimeContext

    context = RuntimeContext(
        trial_id=trial_id,
        data_hubs={},
        stores={},
        startup=None,
    )
    return BrokerOperator.from_dict(
        {
            "actor_id": actor_id,
            "initial_balance": "1000.00",
        },
        context,
    )


def create_nba_test_agent(
    persona_config_path: Path,
    llm_config_path: Path = DEFAULT_LLM_CONFIG_PATH,
    trial_id: str = "test-trial",
):
    """Create NBA BettingAgent with test-specific env vars."""
    from dojozero.nba._agent import BettingAgent
    from dojozero.agents import load_agent_config, create_model, create_formatter

    config = load_agent_config(persona_config_path, llm_config_path)
    # llm is a list of configs - use the first one for tests
    llm_config = config["llm"][0].copy()
    llm_config["api_key_env"] = TEST_API_KEY_ENV
    llm_config["base_url_env"] = TEST_BASE_URL_ENV
    model_type = llm_config.get("model_type", "openai")
    model_name = llm_config.get("model_name", "")
    return BettingAgent(
        actor_id=config["name"],
        trial_id=trial_id,
        name=config["name"],
        sys_prompt=config["sys_prompt"],
        model=create_model(llm_config),
        formatter=create_formatter(model_type, model_name),
    )


def create_nfl_test_agent(
    persona_config_path: Path,
    llm_config_path: Path = DEFAULT_LLM_CONFIG_PATH,
    trial_id: str = "test-trial",
):
    """Create NFL BettingAgent with test-specific env vars."""
    from dojozero.nfl._agent import BettingAgent
    from dojozero.agents import load_agent_config, create_model, create_formatter

    config = load_agent_config(persona_config_path, llm_config_path)
    # llm is a list of configs - use the first one for tests
    llm_config = config["llm"][0].copy()
    llm_config["api_key_env"] = TEST_API_KEY_ENV
    llm_config["base_url_env"] = TEST_BASE_URL_ENV
    model_type = llm_config.get("model_type", "openai")
    model_name = llm_config.get("model_name", "")
    return BettingAgent(
        actor_id=config["name"],
        trial_id=trial_id,
        name=config["name"],
        sys_prompt=config["sys_prompt"],
        model=create_model(llm_config),
        formatter=create_formatter(model_type, model_name),
    )


# Helper to check if integration tests should run
def requires_api_key():
    """Pytest marker for tests requiring API key."""
    return pytest.mark.skipif(
        not os.environ.get(TEST_API_KEY_ENV), reason=f"{TEST_API_KEY_ENV} not set"
    )
