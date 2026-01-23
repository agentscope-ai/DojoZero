from pathlib import Path

import pytest
import yaml

import dojozero.samples  # noqa: F401 - import triggers builder registration

from dojozero import cli as agentx_cli
from dojozero.core import (
    FileSystemOrchestratorStore,
    LocalActorRuntimeProvider,
    TrialSpec,
    get_trial_builder_definition,
)


def test_prepare_trial_spec_applies_metadata() -> None:
    payload = {
        "scenario": {
            "name": "samples.bounded-random",
            "config": {"total_events": 2},
        },
        "metadata": {"extra": "value"},
    }
    spec = agentx_cli._prepare_trial_spec("cli-sample", payload)
    assert isinstance(spec, TrialSpec)
    assert spec.trial_id == "cli-sample"
    assert spec.metadata["extra"] == "value"
    assert spec.metadata["total_events"] == 2


def test_prepare_trial_spec_supports_legacy_environment_key() -> None:
    payload = {
        "environment": {
            "name": "samples.bounded-random",
            "config": {"total_events": 1},
        }
    }
    spec = agentx_cli._prepare_trial_spec("legacy", payload)
    assert isinstance(spec, TrialSpec)
    assert spec.metadata["total_events"] == 1


def test_create_store_uses_filesystem(tmp_path: Path) -> None:
    # Default store directory
    store = agentx_cli._create_store(None)
    assert isinstance(store, FileSystemOrchestratorStore)

    # Custom store directory
    fs_store = agentx_cli._create_store(tmp_path)
    assert isinstance(fs_store, FileSystemOrchestratorStore)


def test_create_runtime_provider_defaults_to_local() -> None:
    provider = agentx_cli._create_runtime_provider("local")
    assert isinstance(provider, LocalActorRuntimeProvider)


def test_create_runtime_provider_with_ray_config(tmp_path: Path) -> None:
    # Create a ray config file
    ray_config = {"auto_init": False, "init_kwargs": {"num_cpus": 2}}
    ray_config_path = tmp_path / "ray_config.yaml"
    with ray_config_path.open("w") as f:
        yaml.safe_dump(ray_config, f)

    # Skip if ray is not installed
    if agentx_cli.RayActorRuntimeProvider is None:
        pytest.skip("Ray is not installed")

    provider = agentx_cli._create_runtime_provider("ray", ray_config_path)
    assert provider is not None


def test_gather_spec_imports_handles_strings_and_lists() -> None:
    assert agentx_cli._gather_imports({"imports": "foo"}) == ("foo",)
    payload = {"imports": ["foo", "bar"]}
    assert agentx_cli._gather_imports(payload) == ("foo", "bar")


def test_prepare_trial_spec_requires_scenario_mapping() -> None:
    payload = {
        "metadata": {"foo": "bar"},
    }
    with pytest.raises(agentx_cli.DojoZeroCLIError):
        agentx_cli._prepare_trial_spec("missing-env", payload)


def test_prepare_trial_spec_surfaces_validation_errors() -> None:
    payload = {
        "scenario": {
            "name": "samples.bounded-random",
            "config": {"total_events": -1},
        },
    }
    with pytest.raises(agentx_cli.DojoZeroCLIError):
        agentx_cli._prepare_trial_spec("bad-config", payload)


def test_generate_example_spec_uses_entry_metadata() -> None:
    entry = get_trial_builder_definition("samples.bounded-random")
    spec = agentx_cli._generate_example_spec("samples.bounded-random", entry)
    assert spec["scenario"]["name"] == "samples.bounded-random"
    assert "total_events" in spec["scenario"]["config"]


def test_write_yaml_file_creates_parent(tmp_path: Path) -> None:
    payload = {"foo": "bar"}
    path = tmp_path / "nested" / "example.yaml"
    agentx_cli._write_yaml_file(path, payload)
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    assert loaded == payload
