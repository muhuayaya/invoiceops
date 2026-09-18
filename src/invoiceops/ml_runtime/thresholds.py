from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .contracts import InferenceResult


@dataclass(frozen=True)
class ThresholdPolicy:
    version: str
    candidate_threshold: float
    auto_accept_threshold: float
    high_risk_labels: frozenset[str]

    @classmethod
    def from_file(cls, path: Path) -> "ThresholdPolicy":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            version=data["version"],
            candidate_threshold=float(data["candidate_threshold"]),
            auto_accept_threshold=float(data["auto_accept_threshold"]),
            high_risk_labels=frozenset(data["high_risk_labels"]),
        )


@dataclass(frozen=True)
class DecisionPolicy:
    thresholds: ThresholdPolicy

    def decide(self, result: InferenceResult) -> tuple[str, tuple[str, ...]]:
        high_risk = [
            prediction
            for prediction in result.predictions
            if prediction.label_code in self.thresholds.high_risk_labels
            and prediction.score >= self.thresholds.candidate_threshold
        ]
        if high_risk:
            return "needs_review", ("HIGH_RISK_LABEL",)
        accepted = [
            prediction
            for prediction in result.predictions
            if prediction.score >= self.thresholds.auto_accept_threshold
        ]
        if not accepted:
            return "needs_review", ("LOW_CONFIDENCE",)
        return "classified", ()
