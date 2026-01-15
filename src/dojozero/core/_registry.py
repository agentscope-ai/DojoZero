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
from ._types import RuntimeContext

ParamModelT = TypeVar("ParamModelT", bound=BaseModel)
TrialBuilderFn = Callable[[str, ParamModelT], TrialSpec]  # trial_id, params
RuntimeContextBuilder = Callable[["TrialSpec"], RuntimeContext]


@dataclass(slots=True)
class TrialBuilderDefinition(Generic[ParamModelT]):
    """Metadata stored for each registered trial builder."""

    name: str
    param_model: type[ParamModelT]
    build_fn: TrialBuilderFn
    description: str | None = None
    example_params: ParamModelT | Mapping[str, Any] | None = None
    context_builder: RuntimeContextBuilder | None = None

    def build(self, trial_id: str, payload: Mapping[str, Any]) -> TrialSpec:
        config = self.param_model.model_validate(payload)
        spec = self.build_fn(trial_id, config)
        # Automatically add builder_name to metadata
        spec.metadata["builder_name"] = self.name
        return spec

    def schema(self) -> Mapping[str, Any]:
        return self.param_model.model_json_schema()

    def example_dict(self) -> Mapping[str, Any]:
        example = self.example_params
        if example is not None:
            if isinstance(example, BaseModel):
                return example.model_dump(mode="python")
            return dict(cast(Mapping[str, Any], example))
        try:
            instance = self.param_model()
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
    param_model: type[ParamModelT],
    builder: TrialBuilderFn,
    *,
    description: str | None = None,
    example_params: ParamModelT | Mapping[str, Any] | None = None,
    context_builder: RuntimeContextBuilder | None = None,
    overwrite: bool = False,
) -> None:
    """Register *builder* under *name* for CLI discovery.

    Args:
        name: Builder identifier
        param_model: Pydantic model for trial parameters
        builder: Function that builds TrialSpec from params
        description: Optional description of the builder
        example_params: Optional example parameters
        context_builder: Optional function to build runtime context (DataHub/Store instances)
        overwrite: Whether to overwrite existing registration
    """

    if not name:
        raise ValueError("builder name cannot be empty")
    if name in _REGISTRY and not overwrite:
        raise TrialBuilderRegistryError(
            f"builder '{name}' is already registered; pass overwrite=True to replace it"
        )
    _REGISTRY[name] = TrialBuilderDefinition(
        name=name,
        param_model=param_model,
        build_fn=builder,
        description=description,
        example_params=example_params,
        context_builder=context_builder,
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
    "ParamModelT",
    "TrialBuilderDefinition",
    "TrialBuilderRegistryError",
    "TrialBuilderNotFoundError",
    "TrialBuilderFn",
    "register_trial_builder",
    "unregister_trial_builder",
    "get_trial_builder_definition",
    "list_trial_builders",
]
