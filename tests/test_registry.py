from collections.abc import Iterator

import pytest

from pydantic import BaseModel

from agentx.core import (
    TrialSpec,
    get_trial_builder_definition,
    list_trial_builders,
    register_trial_builder,
    unregister_trial_builder,
)
from agentx.samples.bounded_random import BoundedRandomTrialConfig


def _bounded_random_spec(trial_id: str, total_events: int) -> TrialSpec:
    builder = get_trial_builder_definition("samples.bounded-random")
    config = BoundedRandomTrialConfig(total_events=total_events)
    return builder.build(trial_id, config.model_dump(mode="python"))


@pytest.fixture
def _cleanup_builder() -> Iterator[str]:
    name = "test.registry"
    unregister_trial_builder(name)
    yield name
    unregister_trial_builder(name)


class _TestConfig(BaseModel):
    total: int = 1


def test_register_and_get_trial_builder(_cleanup_builder: str) -> None:
    name = _cleanup_builder

    def _builder(trial_id: str, config: _TestConfig) -> TrialSpec:
        return _bounded_random_spec(trial_id, config.total)

    register_trial_builder(name, _TestConfig, _builder)
    definition = get_trial_builder_definition(name)
    spec = definition.build("reg-trial", {"total": 2})
    assert spec.trial_id == "reg-trial"
    assert spec.metadata["total_events"] == 2


def test_list_trial_builders_includes_custom(_cleanup_builder: str) -> None:
    name = _cleanup_builder

    def _builder(trial_id: str, config: _TestConfig) -> TrialSpec:
        return _bounded_random_spec(trial_id, config.total)

    register_trial_builder(name, _TestConfig, _builder)
    builders = list_trial_builders()
    assert name in builders


def test_builder_entry_exposes_schema_and_example(_cleanup_builder: str) -> None:
    name = _cleanup_builder

    def _builder(trial_id: str, config: _TestConfig) -> TrialSpec:
        return _bounded_random_spec(trial_id, config.total)

    register_trial_builder(
        name,
        _TestConfig,
        _builder,
        example_config={"total": 3},
    )
    entry = get_trial_builder_definition(name)
    assert entry.schema()["title"] == "_TestConfig"
    assert entry.example_dict()["total"] == 3
