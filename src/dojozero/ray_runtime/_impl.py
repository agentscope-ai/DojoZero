"""Ray-backed runtime provider for DojoZero actors."""

import inspect
import os
from typing import Any, Mapping, TYPE_CHECKING, cast

import ray
from ray.actor import ActorHandle

from dojozero.core._actors import Actor, ActorState
from dojozero.core._runtime import ActorHandler, ActorRuntimeProvider
from dojozero.core._types import ActorContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dojozero.core._dashboard import ActorSpec


async def _await_ref(ref: "ray.ObjectRef[Any]") -> Any:
    # Ray ObjectRefs are awaitable inside async actors; prefer this to avoid
    # blocking the event loop with ``ray.get``.
    return await ref


class RayActorProxy:
    """Proxy object that forwards attribute access to a Ray actor handle."""

    __slots__ = ("_actor_id", "_actor_cls", "_handle")

    def __init__(
        self,
        actor_id: str,
        actor_cls: type[Actor[Any]],
        handle: ActorHandle,
    ) -> None:
        self._actor_id = actor_id
        self._actor_cls = actor_cls
        self._handle = handle

    @classmethod
    def from_dict(
        cls,
        config: Mapping[str, Any],
    ) -> "RayActorProxy":  # pragma: no cover - defensive guard
        raise RuntimeError("RayActorProxy should not be instantiated via from_dict")

    @property
    def actor_id(self) -> str:  # type: ignore[override]
        return self._actor_id

    @property
    def actor_cls(self) -> type[Actor[Any]]:
        return self._actor_cls

    @property
    def handle(self) -> ActorHandle:
        return self._handle

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._actor_cls, name, None)
        if callable(attr):

            async def _method(*args: Any, **kwargs: Any) -> Any:
                return await _await_ref(
                    self._handle.call_method.remote(name, args, kwargs)
                )

            return _method
        # Treat everything else as data attribute / property value.
        return ray.get(self._handle.get_attribute.remote(name))

    def __reduce__(self) -> tuple[Any, tuple[str, type[Actor[Any]], ActorHandle]]:
        return (RayActorProxy, (self._actor_id, self._actor_cls, self._handle))

    async def start(self) -> None:
        await _await_ref(self._handle.start.remote())

    async def stop(self) -> None:
        await _await_ref(self._handle.stop.remote())

    async def save_state(self) -> ActorState:
        return await _await_ref(self._handle.save_state.remote())

    async def load_state(self, state: ActorState) -> None:
        await _await_ref(self._handle.load_state.remote(state))


@ray.remote
class _RayActorHost:
    """Ray actor that owns the real actor instance."""

    def __init__(self) -> None:
        self._actor: Actor[Any] | None = None

    async def bootstrap(
        self,
        actor_cls: type[Actor[Any]],
        config: Mapping[str, Any],
        actor_id: str,
        resume_state: ActorState | None,
        context: ActorContext,
    ) -> None:
        actor = actor_cls.from_dict(config, context)
        if actor.actor_id != actor_id:
            raise ValueError(
                f"actor id mismatch: spec '{actor_id}' != instance '{actor.actor_id}'"
            )
        if resume_state is not None:
            await actor.load_state(resume_state)
        self._actor = actor

    async def start(self) -> None:
        actor = self._require_actor()
        await actor.start()

    async def stop(self) -> None:
        actor = self._require_actor()
        await actor.stop()

    async def save_state(self) -> ActorState:
        actor = self._require_actor()
        return await actor.save_state()

    async def load_state(self, state: ActorState) -> None:
        actor = self._require_actor()
        await actor.load_state(state)

    async def call_method(
        self, name: str, args: tuple[Any, ...], kwargs: Mapping[str, Any]
    ) -> Any:
        actor = self._require_actor()
        target = getattr(actor, name)
        result = target(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def get_attribute(self, name: str) -> Any:
        actor = self._require_actor()
        value = getattr(actor, name)
        if inspect.isawaitable(value):
            value = await value
        return value

    def _require_actor(self) -> Actor[Any]:
        if self._actor is None:  # pragma: no cover - defensive
            raise RuntimeError("actor host not initialized")
        return self._actor


class RayActorHandler(ActorHandler):
    """Actor handler that drives an actor running inside Ray."""

    __slots__ = ("_actor_id", "_actor_cls", "_handle", "_proxy")

    def __init__(
        self,
        *,
        actor_id: str,
        actor_cls: type[Actor[Any]],
        handle: ActorHandle,
    ) -> None:
        self._actor_id = actor_id
        self._actor_cls = actor_cls
        self._handle = handle
        self._proxy = RayActorProxy(self._actor_id, self._actor_cls, self._handle)

    @property
    def instance(self) -> Actor[Any]:
        return cast(Actor[Any], self._proxy)

    @property
    def actor_id(self) -> str:
        return self._actor_id

    @property
    def handle(self) -> ActorHandle:
        return self._handle

    async def start(self) -> None:
        await _await_ref(self.handle.start.remote())

    async def stop(self) -> None:
        await _await_ref(self.handle.stop.remote())

    async def save_state(self) -> ActorState:
        return await _await_ref(self.handle.save_state.remote())

    async def load_state(self, state: ActorState) -> None:
        await _await_ref(self.handle.load_state.remote(state))


class RayActorRuntimeProvider(ActorRuntimeProvider):
    """Runtime provider that instantiates actors as Ray actors."""

    def __init__(
        self,
        *,
        auto_init: bool = True,
        init_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        self._auto_init = auto_init
        self._init_kwargs = dict(init_kwargs or {})
        # Disable the Ray dashboard/metrics exporter unless explicitly requested,
        # since it is unnecessary for local CLI runs and tends to emit noisy
        # connection errors in constrained environments.
        self._init_kwargs.setdefault("include_dashboard", False)

    def _ensure_ray(self) -> None:
        if ray.is_initialized():
            return
        if not self._auto_init:
            raise RuntimeError("Ray is not initialized and auto_init is False")
        os.environ.setdefault("RAY_ACCEL_ENV_VAR_OVERRIDE_ON_ZERO", "0")
        ray.init(**self._init_kwargs)

    async def create_handler(
        self,
        spec: "ActorSpec[Any]",
        context: ActorContext,
    ) -> RayActorHandler:
        self._ensure_ray()
        handle = cast(ActorHandle, _RayActorHost.remote())
        await _await_ref(
            handle.bootstrap.remote(
                spec.actor_cls,
                spec.config,
                spec.actor_id,
                spec.resume_state,
                context,
            )
        )
        return RayActorHandler(
            actor_id=spec.actor_id,
            actor_cls=spec.actor_cls,
            handle=handle,
        )


__all__ = [
    "RayActorProxy",
    "RayActorHandler",
    "RayActorRuntimeProvider",
]
