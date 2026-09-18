from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class LabelScore:
    label_code: str
    score: float


@dataclass(frozen=True)
class InferenceResult:
    predictions: tuple[LabelScore, ...]
    language: str
    model_version: str
    threshold_version: str
    inference_ms: float
    sanitized: bool = True
    metadata: dict[str, str] = field(default_factory=dict)


class ModelRuntime(Protocol):
    """Stable interface shared by baseline and transformer runtimes."""

    model_version: str

    def predict(self, text: str, *, sanitized: bool = True) -> InferenceResult:
        ...
