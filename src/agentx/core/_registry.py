"""Registry utilities for discovering trial-spec builders at runtime."""

from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Generic,
    Mapping,
    MutableMapping,
    Sequence,
    TypeVar,
    cast,
)

from pydantic import BaseModel

from ._dashboard import TrialSpec

ConfigModelT = TypeVar("ConfigModelT", bound=BaseModel)
TrialBuilderFn = Callable[[str, ConfigModelT], TrialSpec]


@dataclass(slots=True)
class TrialBuilderDefinition(Generic[ConfigModelT]):
    """Metadata stored for each registered trial builder."""

    name: str
    config_model: type[ConfigModelT]
    build_fn: TrialBuilderFn
    description: str | None = None
    example_config: ConfigModelT | Mapping[str, Any] | None = None

    def build(self, trial_id: str, payload: Mapping[str, Any]) -> TrialSpec:
        config = self.config_model.model_validate(payload)
        return self.build_fn(trial_id, config)

    def schema(self) -> Mapping[str, Any]:
        return self.config_model.model_json_schema()

    def example_dict(self) -> Mapping[str, Any]:
        example = self.example_config
        if example is not None:
            if isinstance(example, BaseModel):
                return example.model_dump(mode="python")
            return dict(cast(Mapping[str, Any], example))
        try:
            instance = self.config_model()
        except Exception:  # pragma: no cover - best effort default
            return {}
        return instance.model_dump(mode="python")


class TrialBuilderRegistryError(RuntimeError):
    """Base error raised when interacting with the trial builder registry."""


class TrialBuilderNotFoundError(TrialBuilderRegistryError):
    """Raised when the requested builder has not been registered."""


_REGISTRY: MutableMapping[str, TrialBuilderDefinition[Any]] = {}


def register_trial_builder(
    name: str,
    config_model: type[ConfigModelT],
    builder: TrialBuilderFn,
    *,
    description: str | None = None,
    example_config: ConfigModelT | Mapping[str, Any] | None = None,
    overwrite: bool = False,
) -> None:
    """Register *builder* under *name* for CLI discovery."""

    if not name:
        raise ValueError("builder name cannot be empty")
    if name in _REGISTRY and not overwrite:
        raise TrialBuilderRegistryError(
            f"builder '{name}' is already registered; pass overwrite=True to replace it"
        )
    _REGISTRY[name] = TrialBuilderDefinition(
        name=name,
        config_model=config_model,
        build_fn=builder,
        description=description,
        example_config=example_config,
    )


def unregister_trial_builder(name: str) -> None:
    """Remove *name* from the registry if present."""

    _REGISTRY.pop(name, None)


def get_trial_builder_definition(name: str) -> TrialBuilderDefinition[Any]:
    try:
        return _REGISTRY[name]
    except KeyError as exc:  # pragma: no cover - defensive
        raise TrialBuilderNotFoundError(
            f"no trial builder registered under '{name}'"
        ) from exc


def list_trial_builders() -> Sequence[str]:
    """Return the currently registered builder names."""

    return tuple(sorted(_REGISTRY.keys()))


__all__ = [
    "ConfigModelT",
    "TrialBuilderDefinition",
    "TrialBuilderRegistryError",
    "TrialBuilderNotFoundError",
    "TrialBuilderFn",
    "register_trial_builder",
    "unregister_trial_builder",
    "get_trial_builder_definition",
    "list_trial_builders",
]
