"""Small, deterministic per-label threshold calibration utilities."""
from __future__ import annotations

from typing import Sequence

import numpy as np
from sklearn.metrics import f1_score


def calibrate_thresholds(
    y_true: Sequence[Sequence[int]],
    probabilities: Sequence[Sequence[float]],
    label_names: Sequence[str],
    *,
    grid: Sequence[float] = tuple(round(value / 100, 2) for value in range(30, 91, 5)),
) -> dict[str, float]:
    truth = np.asarray(y_true, dtype=int)
    scores = np.asarray(probabilities, dtype=float)
    if truth.shape != scores.shape or truth.shape[1] != len(label_names):
        raise ValueError("truth, probabilities, and label_names must have compatible shapes")
    result: dict[str, float] = {}
    for index, label in enumerate(label_names):
        best = max(
            grid,
            key=lambda threshold: (f1_score(truth[:, index], scores[:, index] >= threshold, zero_division=0), -threshold),
        )
        result[label] = float(best)
    return result


def calibrate_micro_thresholds(
    y_true: Sequence[Sequence[int]],
    probabilities: Sequence[Sequence[float]],
    label_names: Sequence[str],
    *,
    grid: Sequence[float] = tuple(round(value / 100, 2) for value in range(30, 91, 5)),
    rounds: int = 3,
) -> dict[str, float]:
    """Tune per-label thresholds on dev data using global micro-F1 only."""
    truth = np.asarray(y_true, dtype=int)
    scores = np.asarray(probabilities, dtype=float)
    if truth.shape != scores.shape or truth.shape[1] != len(label_names):
        raise ValueError("truth, probabilities, and label_names must have compatible shapes")
    thresholds = calibrate_thresholds(truth, scores, label_names, grid=grid)

    def objective(candidate: dict[str, float]) -> float:
        vector = np.asarray([candidate[label] for label in label_names])
        return float(f1_score(truth, scores >= vector, average="micro", zero_division=0))

    for _ in range(rounds):
        changed = False
        for label in label_names:
            current = thresholds[label]
            best = max(
                grid,
                key=lambda threshold: (
                    objective({**thresholds, label: float(threshold)}),
                    -abs(float(threshold) - current),
                    -float(threshold),
                ),
            )
            if float(best) != current:
                thresholds[label] = float(best)
                changed = True
        if not changed:
            break
    return thresholds


def rejection_reasons(
    labels: Sequence[str],
    scores: Sequence[float],
    thresholds: dict[str, float],
    high_risk_labels: set[str],
) -> tuple[str, ...]:
    accepted = [label for label, score in zip(labels, scores) if score >= thresholds.get(label, 1.0)]
    if any(label in high_risk_labels for label in accepted):
        return ("HIGH_RISK_LABEL",)
    if not accepted:
        return ("LOW_CONFIDENCE",)
    if "OTHER_REVIEW" in accepted and len(accepted) > 1:
        return ("CONFLICTING_LABELS",)
    return ()
