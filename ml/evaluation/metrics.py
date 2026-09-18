"""Model-independent multilabel metrics and performance measurements."""
from __future__ import annotations

import pickle
from statistics import median
from time import perf_counter
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from sklearn.metrics import f1_score, hamming_loss, precision_recall_fscore_support


def _as_matrix(values: Sequence[Sequence[int]]) -> np.ndarray:
    return np.asarray(values, dtype=int)


def classification_metrics(
    y_true: Sequence[Sequence[int]],
    y_pred: Sequence[Sequence[int]],
    label_names: Sequence[str],
) -> dict[str, Any]:
    true_matrix, pred_matrix = _as_matrix(y_true), _as_matrix(y_pred)
    precision, recall, f1, support = precision_recall_fscore_support(
        true_matrix,
        pred_matrix,
        average=None,
        zero_division=0,
    )
    return {
        "micro_f1": float(f1_score(true_matrix, pred_matrix, average="micro", zero_division=0)),
        "macro_f1": float(f1_score(true_matrix, pred_matrix, average="macro", zero_division=0)),
        "hamming_loss": float(hamming_loss(true_matrix, pred_matrix)),
        "per_label": {
            label: {
                "precision": float(precision[index]),
                "recall": float(recall[index]),
                "f1": float(f1[index]),
                "support": int(support[index]),
            }
            for index, label in enumerate(label_names)
        },
    }


def benchmark_latency(predict: Callable[[str], Any], texts: Iterable[str]) -> dict[str, float]:
    timings_ms: list[float] = []
    for text in texts:
        started = perf_counter()
        predict(text)
        timings_ms.append((perf_counter() - started) * 1000)
    if not timings_ms:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0}
    ordered = sorted(timings_ms)
    p95_index = min(len(ordered) - 1, max(0, int(np.ceil(len(ordered) * 0.95)) - 1))
    return {
        "count": len(ordered),
        "p50_ms": float(median(ordered)),
        "p95_ms": float(ordered[p95_index]),
        "mean_ms": float(sum(ordered) / len(ordered)),
    }


def serialized_size_bytes(*objects: Any) -> int:
    return len(pickle.dumps(objects, protocol=pickle.HIGHEST_PROTOCOL))


def evaluate_by_language(
    rows: Sequence[dict[str, Any]],
    y_true: Sequence[Sequence[int]],
    y_pred: Sequence[Sequence[int]],
    label_names: Sequence[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    languages = sorted({str(row.get("language", "unknown")) for row in rows})
    for language in languages:
        indexes = [index for index, row in enumerate(rows) if row.get("language") == language]
        result[language] = classification_metrics(
            [y_true[index] for index in indexes],
            [y_pred[index] for index in indexes],
            label_names,
        )
    return result
