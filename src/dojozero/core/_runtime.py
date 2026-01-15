"""Actor runtime abstractions for plugging different execution backends."""

from dataclasses import dataclass
from typing import Any, Protocol, TYPE_CHECKING

from ._actors import Actor, ActorState
from ._types import ActorContext

if TYPE_CHECKING:  # pragma: no cover - import-time circular guard
    from ._dashboard import ActorSpec


class ActorHandler(Protocol):
    """Backend-specific controller for a concrete actor instance."""

    @property
    def actor_id(self) -> str: ...

    @property
    def instance(self) -> Actor[Any]: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def save_state(self) -> ActorState: ...

    async def load_state(self, state: ActorState) -> None: ...


class ActorRuntimeProvider(Protocol):
    """Factory that turns :class:`ActorSpec` declarations into handlers."""

    async def create_handler(
        self, spec: "ActorSpec[Any]", context: ActorContext
    ) -> ActorHandler: ...


@dataclass(slots=True)
class LocalActorHandler:
    """In-process handler that directly owns the actor instance."""

    _instance: Actor[Any]

    @property
    def actor_id(self) -> str:
        return self._instance.actor_id

    @property
    def instance(self) -> Actor[Any]:
        return self._instance

    async def start(self) -> None:
        await self._instance.start()

    async def stop(self) -> None:
        await self._instance.stop()

    async def save_state(self) -> ActorState:
        return await self._instance.save_state()

    async def load_state(self, state: ActorState) -> None:
        await self._instance.load_state(state)


class LocalActorRuntimeProvider(ActorRuntimeProvider):
    """Default runtime provider that materializes actors locally."""

    async def create_handler(
        self,
        spec: "ActorSpec[Any]",
        context: ActorContext,
    ) -> LocalActorHandler:
        if not hasattr(spec.actor_cls, "from_dict"):
            raise TypeError(f"actor class {spec.actor_cls} has no from_dict method")

        actor = spec.actor_cls.from_dict(spec.config, context)

        if actor.actor_id != spec.actor_id:
            raise ValueError(
                f"actor id mismatch: spec '{spec.actor_id}' != instance '{actor.actor_id}'"
            )

        handler = LocalActorHandler(actor)
        if spec.resume_state is not None:
            await handler.load_state(spec.resume_state)
        return handler


__all__ = [
    "ActorContext",
    "ActorHandler",
    "ActorRuntimeProvider",
    "LocalActorHandler",
    "LocalActorRuntimeProvider",
]
