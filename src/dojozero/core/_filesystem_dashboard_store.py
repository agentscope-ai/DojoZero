"""File-system backed implementation of :class:`DashboardStore`."""

import json
import shutil
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Mapping, Sequence, Type, cast
from urllib.parse import quote
from uuid import uuid4

from ._actors import Actor
from ._dashboard import (
    ActorPhase,
    ActorRole,
    ActorSpec,
    AgentSpec,
    ActorStatus,
    CheckpointNotFoundError,
    CheckpointSummary,
    DataStreamSpec,
    DashboardStore,
    OperatorSpec,
    TrialCheckpoint,
    TrialPhase,
    TrialRecord,
    TrialSpec,
    TrialStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FileSystemDashboardStore(DashboardStore):
    """Persist trials, statuses, and checkpoints using JSON files."""

    SPEC_FILE = "spec.json"
    STATUS_FILE = "status.json"
    CHECKPOINT_DIR = "checkpoints"
    CHECKPOINT_INDEX = "checkpoint_index.json"
    TRIAL_INDEX = "trials_index.json"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()
        self._trials_dir = self._root / "trials"
        self._index_file = self._root / self.TRIAL_INDEX
        self._root.mkdir(parents=True, exist_ok=True)
        self._trials_dir.mkdir(parents=True, exist_ok=True)
        if not self._index_file.exists():
            self._write_json(self._index_file, {"trials": []})

    # ------------------------------------------------------------------
    # DashboardStore API
    # ------------------------------------------------------------------

    def list_trial_records(self) -> Sequence[TrialRecord]:
        records: list[TrialRecord] = []
        for trial_id in self._load_trial_ids():
            record = self.get_trial_record(trial_id)
            if record is not None:
                records.append(record)
        return tuple(records)

    def get_trial_record(self, trial_id: str) -> TrialRecord | None:
        trial_dir = self._trial_dir(trial_id)
        spec_path = trial_dir / self.SPEC_FILE
        if not spec_path.exists():
            return None
        spec = self._read_spec(spec_path)
        status_path = trial_dir / self.STATUS_FILE
        status = self._read_status(status_path) if status_path.exists() else None
        return TrialRecord(spec=spec, last_status=status)

    def upsert_trial_record(self, record: TrialRecord) -> None:
        trial_dir = self._trial_dir(record.trial_id)
        trial_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(trial_dir / self.SPEC_FILE, self._serialize_spec(record.spec))
        if record.last_status is not None:
            self._write_json(
                trial_dir / self.STATUS_FILE, self._serialize_status(record.last_status)
            )
        else:
            try:
                (trial_dir / self.STATUS_FILE).unlink()
            except FileNotFoundError:  # pragma: no cover - best effort cleanup
                pass
        trial_ids = self._load_trial_ids()
        if record.trial_id not in trial_ids:
            trial_ids.append(record.trial_id)
            self._write_json(self._index_file, {"trials": trial_ids})
        # Ensure checkpoint index file exists for new trials
        checkpoint_index = trial_dir / self.CHECKPOINT_INDEX
        if not checkpoint_index.exists():
            self._write_json(checkpoint_index, [])

    def delete_trial_record(self, trial_id: str) -> None:
        trial_dir = self._trial_dir(trial_id)
        if trial_dir.exists():
            shutil.rmtree(trial_dir)
        trial_ids = [tid for tid in self._load_trial_ids() if tid != trial_id]
        self._write_json(self._index_file, {"trials": trial_ids})

    def save_checkpoint(self, checkpoint: TrialCheckpoint) -> TrialCheckpoint:
        checkpoint_id = checkpoint.checkpoint_id or self._generate_checkpoint_id()
        created_at = checkpoint.created_at or _utcnow()
        persisted = TrialCheckpoint(
            trial_id=checkpoint.trial_id,
            actor_states={
                actor_id: dict(state)
                for actor_id, state in checkpoint.actor_states.items()
            },
            checkpoint_id=checkpoint_id,
            created_at=created_at,
        )
        trial_dir = self._trial_dir(checkpoint.trial_id)
        trial_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_dir = trial_dir / self.CHECKPOINT_DIR
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoint_dir / f"{checkpoint_id}.json"
        self._write_json(checkpoint_path, self._serialize_checkpoint(persisted))
        summaries = list(self._load_checkpoint_summaries(trial_dir))
        summaries = [
            summary for summary in summaries if summary.checkpoint_id != checkpoint_id
        ]
        summaries.append(
            CheckpointSummary(
                checkpoint_id=checkpoint_id,
                trial_id=checkpoint.trial_id,
                created_at=created_at,
            )
        )
        summaries.sort(key=lambda summary: summary.created_at)
        self._write_json(
            trial_dir / self.CHECKPOINT_INDEX,
            [self._serialize_checkpoint_summary(summary) for summary in summaries],
        )
        return persisted

    def load_checkpoint(self, checkpoint_id: str) -> TrialCheckpoint:
        for trial_id in self._load_trial_ids():
            trial_dir = self._trial_dir(trial_id)
            checkpoint_dir = trial_dir / self.CHECKPOINT_DIR
            checkpoint_path = checkpoint_dir / f"{checkpoint_id}.json"
            if checkpoint_path.exists():
                return self._read_checkpoint(checkpoint_path)
        raise CheckpointNotFoundError(f"checkpoint '{checkpoint_id}' not found")

    def list_checkpoints(self, trial_id: str) -> Sequence[CheckpointSummary]:
        trial_dir = self._trial_dir(trial_id)
        return tuple(self._load_checkpoint_summaries(trial_dir))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_trial_ids(self) -> list[str]:
        if not self._index_file.exists():
            return []
        payload = self._read_json(self._index_file)
        assert isinstance(payload, dict), "Trial index file must contain a dict"
        trials = payload.get("trials", [])
        assert isinstance(trials, list), "Trials must be a list"
        return trials

    def _trial_dir(self, trial_id: str) -> Path:
        safe_name = quote(trial_id, safe="")
        return self._trials_dir / safe_name

    @staticmethod
    def _generate_checkpoint_id() -> str:
        return uuid4().hex

    # JSON (de-)serialization -------------------------------------------------

    def _serialize_spec(self, spec: TrialSpec) -> dict[str, Any]:
        return {
            "trial_id": spec.trial_id,
            "metadata": dict(spec.metadata),
            "operators": [
                self._serialize_operator_spec(actor) for actor in spec.operators
            ],
            "agents": [self._serialize_agent_spec(actor) for actor in spec.agents],
            "data_streams": [
                self._serialize_data_stream_spec(stream) for stream in spec.data_streams
            ],
        }

    def _read_spec(self, path: Path) -> TrialSpec:
        payload = self._read_json(path)
        return TrialSpec(
            trial_id=str(payload["trial_id"]),
            metadata=dict(payload.get("metadata", {})),
            operators=tuple(
                self._deserialize_operator_spec(item)
                for item in payload.get("operators", [])
            ),
            agents=tuple(
                self._deserialize_agent_spec(item) for item in payload.get("agents", [])
            ),
            data_streams=tuple(
                self._deserialize_data_stream_spec(item)
                for item in payload.get("data_streams", [])
            ),
        )

    def _serialize_operator_spec(self, spec: OperatorSpec[Any]) -> dict[str, Any]:
        payload = self._serialize_actor_spec_common(spec)
        if spec.agent_ids:
            payload["agent_ids"] = list(spec.agent_ids)
        if spec.data_stream_ids:
            payload["data_stream_ids"] = list(spec.data_stream_ids)
        return payload

    def _serialize_agent_spec(self, spec: AgentSpec[Any]) -> dict[str, Any]:
        payload = self._serialize_actor_spec_common(spec)
        if spec.operator_ids:
            payload["operator_ids"] = list(spec.operator_ids)
        if spec.data_stream_ids:
            payload["data_stream_ids"] = list(spec.data_stream_ids)
        return payload

    def _serialize_data_stream_spec(self, spec: DataStreamSpec[Any]) -> dict[str, Any]:
        payload = self._serialize_actor_spec_common(spec)
        if spec.consumer_ids:
            payload["consumer_ids"] = list(spec.consumer_ids)
        return payload

    def _serialize_actor_spec_common(self, spec: ActorSpec[Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "actor_id": spec.actor_id,
            "actor_cls": self._actor_cls_path(spec.actor_cls),
            "config": dict(spec.config),
        }
        if spec.resume_state is not None:
            payload["resume_state"] = dict(spec.resume_state)
        return payload

    def _deserialize_operator_spec(
        self, payload: Mapping[str, Any]
    ) -> OperatorSpec[Any]:
        base_kwargs = self._deserialize_actor_spec_common(payload)
        agent_ids = tuple(str(value) for value in payload.get("agent_ids", ()))
        data_stream_ids = tuple(
            str(value) for value in payload.get("data_stream_ids", ())
        )
        return OperatorSpec(
            agent_ids=agent_ids, data_stream_ids=data_stream_ids, **base_kwargs
        )

    def _deserialize_agent_spec(self, payload: Mapping[str, Any]) -> AgentSpec[Any]:
        base_kwargs = self._deserialize_actor_spec_common(payload)
        operator_ids = tuple(str(value) for value in payload.get("operator_ids", ()))
        data_stream_ids = tuple(
            str(value) for value in payload.get("data_stream_ids", ())
        )
        return AgentSpec(
            operator_ids=operator_ids, data_stream_ids=data_stream_ids, **base_kwargs
        )

    def _deserialize_data_stream_spec(
        self, payload: Mapping[str, Any]
    ) -> DataStreamSpec[Any]:
        base_kwargs = self._deserialize_actor_spec_common(payload)
        consumer_ids = tuple(str(value) for value in payload.get("consumer_ids", ()))
        return DataStreamSpec(consumer_ids=consumer_ids, **base_kwargs)

    def _deserialize_actor_spec_common(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        actor_cls = self._resolve_actor_cls(str(payload["actor_cls"]))
        resume_state = payload.get("resume_state")
        if resume_state is not None:
            resume_state = dict(resume_state)
        config = dict(cast(Mapping[str, Any], payload.get("config", {})))
        return {
            "actor_id": str(payload["actor_id"]),
            "actor_cls": actor_cls,
            "config": config,
            "resume_state": resume_state,
        }

    def _serialize_status(self, status: TrialStatus) -> dict[str, Any]:
        return {
            "trial_id": status.trial_id,
            "phase": status.phase.value,
            "actors": [
                {
                    "actor_id": actor.actor_id,
                    "role": actor.role.value,
                    "phase": actor.phase.value,
                    "last_error": actor.last_error,
                }
                for actor in status.actors
            ],
            "metadata": dict(status.metadata),
            "last_error": status.last_error,
        }

    def _read_status(self, path: Path) -> TrialStatus:
        payload = self._read_json(path)
        return TrialStatus(
            trial_id=str(payload["trial_id"]),
            phase=TrialPhase(str(payload["phase"])),
            actors=tuple(
                ActorStatus(
                    actor_id=str(item["actor_id"]),
                    role=ActorRole(str(item["role"])),
                    phase=ActorPhase(str(item["phase"])),
                    last_error=item.get("last_error"),
                )
                for item in payload.get("actors", [])
            ),
            metadata=dict(payload.get("metadata", {})),
            last_error=payload.get("last_error"),
        )

    def _serialize_checkpoint(self, checkpoint: TrialCheckpoint) -> dict[str, Any]:
        created_at = checkpoint.created_at or _utcnow()
        return {
            "trial_id": checkpoint.trial_id,
            "actor_states": {
                actor_id: dict(state)
                for actor_id, state in checkpoint.actor_states.items()
            },
            "checkpoint_id": checkpoint.checkpoint_id,
            "created_at": created_at.isoformat(),
        }

    def _read_checkpoint(self, path: Path) -> TrialCheckpoint:
        payload = self._read_json(path)
        checkpoint_id = payload.get("checkpoint_id")
        return TrialCheckpoint(
            trial_id=str(payload["trial_id"]),
            actor_states={
                actor_id: dict(state)
                for actor_id, state in payload.get("actor_states", {}).items()
            },
            checkpoint_id=str(checkpoint_id) if checkpoint_id is not None else None,
            created_at=self._parse_datetime(payload.get("created_at")),
        )

    def _load_checkpoint_summaries(
        self, trial_dir: Path
    ) -> Sequence[CheckpointSummary]:
        index_path = trial_dir / self.CHECKPOINT_INDEX
        if not index_path.exists():
            return ()
        payload = self._read_json(index_path)
        return tuple(
            CheckpointSummary(
                checkpoint_id=str(item["checkpoint_id"]),
                trial_id=str(item.get("trial_id", trial_dir.name)),
                created_at=self._parse_datetime(item.get("created_at")) or _utcnow(),
            )
            for item in payload
        )

    def _serialize_checkpoint_summary(
        self, summary: CheckpointSummary
    ) -> dict[str, Any]:
        return {
            "checkpoint_id": summary.checkpoint_id,
            "trial_id": summary.trial_id,
            "created_at": summary.created_at.isoformat(),
        }

    @staticmethod
    def _actor_cls_path(actor_cls: Type[Actor]) -> str:
        return f"{actor_cls.__module__}:{actor_cls.__qualname__}"

    @staticmethod
    def _resolve_actor_cls(path: str) -> Type[Actor]:
        module_name, _, qualname = path.partition(":")
        if not module_name or not qualname:
            raise ValueError(f"Invalid actor class path '{path}'")
        module = import_module(module_name)
        obj: Any = module
        for attr in qualname.split("."):
            obj = getattr(obj, attr)
        if not isinstance(obj, type):
            raise TypeError(f"Resolved object '{path}' is not a class")
        return cast(Type[Actor], obj)

    def _read_json(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Use unique tmp filename to avoid race conditions when multiple processes
        # write to the same file simultaneously
        tmp_path = path.with_suffix(f".{uuid4().hex}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        tmp_path.replace(path)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt


__all__ = ["FileSystemDashboardStore"]
