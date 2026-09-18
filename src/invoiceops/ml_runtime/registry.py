from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelVersion:
    version: str
    model_type: str
    metrics: dict[str, float]
    threshold_version: str
    status: str = "candidate"


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, ModelVersion] = {}
        self._active: str | None = None
        self._previous: str | None = None

    @property
    def active(self) -> ModelVersion | None:
        return self._models.get(self._active) if self._active else None

    def register(self, model: ModelVersion) -> None:
        if model.version in self._models:
            raise ValueError(f"model already registered: {model.version}")
        self._models[model.version] = model

    def promote(self, version: str) -> ModelVersion:
        candidate = self._models[version]
        if candidate.metrics.get("macro_f1", 0.0) < 0.80:
            raise ValueError("promotion rejected: macro_f1 below 0.80")
        if candidate.metrics.get("micro_f1", 0.0) < 0.85:
            raise ValueError("promotion rejected: micro_f1 below 0.85")
        if candidate.metrics.get("supplier_master_change_recall", 0.0) < 0.95:
            raise ValueError("promotion rejected: critical recall below 0.95")
        if self._active:
            self._previous = self._active
        promoted = ModelVersion(**{**candidate.__dict__, "status": "active"})
        self._models[version] = promoted
        self._active = version
        return promoted

    def rollback(self) -> ModelVersion:
        if not self._previous:
            raise ValueError("rollback unavailable: no previous stable model")
        current = self._models[self._active] if self._active else None
        previous = self._models[self._previous]
        if current:
            self._models[current.version] = ModelVersion(**{**current.__dict__, "status": "rolled_back"})
        restored = ModelVersion(**{**previous.__dict__, "status": "active"})
        self._models[restored.version] = restored
        self._active, self._previous = restored.version, None
        return restored
