from collections.abc import Iterator

import pytest

from pydantic import BaseModel

from dojozero.core import (
    BaseTrialMetadata,
    TrialSpec,
    get_trial_builder_definition,
    list_trial_builders,
    register_trial_builder,
    unregister_trial_builder,
)


@pytest.fixture
def _cleanup_builder() -> Iterator[str]:
    name = "test.registry"
    unregister_trial_builder(name)
    yield name
    unregister_trial_builder(name)


class _TestConfig(BaseModel):
    total: int = 1


def _make_test_spec(trial_id: str, total: int) -> TrialSpec[BaseTrialMetadata]:
    """Create a simple test spec with minimal metadata."""
    metadata = BaseTrialMetadata(
        hub_id="test_hub",
        persistence_file="/tmp/test.jsonl",
        store_types=(),
    )
    return TrialSpec(
        trial_id=trial_id,
        metadata=metadata,
    )


def test_register_and_get_trial_builder(_cleanup_builder: str) -> None:
    name = _cleanup_builder

    def _builder(trial_id: str, config: _TestConfig) -> TrialSpec[BaseTrialMetadata]:
        return _make_test_spec(trial_id, config.total)

    register_trial_builder(name, _TestConfig, _builder)
    definition = get_trial_builder_definition(name)
    spec = definition.build("reg-trial", {"total": 2})
    assert spec.trial_id == "reg-trial"
    assert spec.metadata.hub_id == "test_hub"


def test_list_trial_builders_includes_custom(_cleanup_builder: str) -> None:
    name = _cleanup_builder

    def _builder(trial_id: str, config: _TestConfig) -> TrialSpec[BaseTrialMetadata]:
        return _make_test_spec(trial_id, config.total)

    register_trial_builder(name, _TestConfig, _builder)
    builders = list_trial_builders()
    assert name in builders


def test_builder_entry_exposes_schema_and_example(_cleanup_builder: str) -> None:
    name = _cleanup_builder

    def _builder(trial_id: str, config: _TestConfig) -> TrialSpec[BaseTrialMetadata]:
        return _make_test_spec(trial_id, config.total)

    register_trial_builder(
        name,
        _TestConfig,
        _builder,
        example_params={"total": 3},
    )
    entry = get_trial_builder_definition(name)
    assert entry.schema()["title"] == "_TestConfig"
    assert entry.example_dict()["total"] == 3
