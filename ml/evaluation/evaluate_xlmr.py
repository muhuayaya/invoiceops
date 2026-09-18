"""Evaluate a trained XLM-R artifact on the frozen InvoiceOps test split."""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import time
import tracemalloc
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from ml.data.quality import LABELS, sha256_file
from ml.evaluation.calibration import calibrate_micro_thresholds
from ml.evaluation.metrics import classification_metrics, evaluate_by_language


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["labels"] = [label for label in row.get("labels", "").split("|") if label]
    return rows


def load_model(model_dir: Path, max_length: int):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir, local_files_only=True)
    model.eval()

    def predict(texts: list[str]) -> np.ndarray:
        import torch

        encoded = tokenizer(
            texts,
            truncation=True,
            padding=True,
            max_length=max_length,
            return_tensors="pt",
        )
        with torch.no_grad():
            logits = model(**encoded).logits
        return torch.sigmoid(logits).cpu().numpy()

    return model, predict


def _labels_matrix(rows: list[dict[str, Any]], label_names: tuple[str, ...]) -> np.ndarray:
    return np.asarray([[int(label in row["labels"]) for label in label_names] for row in rows], dtype=int)


def evaluate(model_dir: Path, dataset_path: Path, output_path: Path, runs: int = 500) -> dict[str, Any]:
    rows = load_rows(dataset_path)
    train_rows = [row for row in rows if row.get("split") == "train"]
    dev_rows = [row for row in rows if row.get("split") == "dev"]
    test_rows = [row for row in rows if row.get("split") == "test"]
    if not dev_rows or not test_rows:
        raise ValueError("dataset must contain dev and test rows")

    record_path = model_dir / "training-record.json"
    training_record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {}
    label_names = tuple(training_record.get("labels", sorted(LABELS)))
    config_path = model_dir / "training-config.yaml"
    max_length = 256
    if config_path.is_file():
        for line in config_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("max_length:"):
                max_length = int(line.split(":", 1)[1].strip())
                break
    model, predict = load_model(model_dir, max_length=max_length)
    dev_probabilities = predict([row["text"] for row in dev_rows])
    test_probabilities = predict([row["text"] for row in test_rows])
    thresholds = calibrate_micro_thresholds(
        _labels_matrix(dev_rows, label_names),
        dev_probabilities,
        label_names,
        grid=tuple(round(value / 100, 2) for value in range(30, 91, 5)),
    )
    threshold_vector = np.asarray([thresholds[label] for label in label_names])
    test_predictions = (test_probabilities >= threshold_vector).astype(int)
    truth = _labels_matrix(test_rows, label_names)
    report = classification_metrics(truth, test_predictions, label_names)
    report["language_groups"] = evaluate_by_language(test_rows, truth, test_predictions, label_names)
    report["thresholds"] = thresholds

    benchmark_texts = [test_rows[index % len(test_rows)]["text"] for index in range(max(1, runs))]
    predict(benchmark_texts[:1])
    timings_ms: list[float] = []
    started_cpu = time.process_time()
    tracemalloc.start()
    for text in benchmark_texts:
        started = time.perf_counter()
        predict([text])
        timings_ms.append((time.perf_counter() - started) * 1000)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    ordered = sorted(timings_ms)
    p50_index = min(len(ordered) - 1, max(0, int(np.ceil(len(ordered) * 0.50)) - 1))
    p95_index = min(len(ordered) - 1, max(0, int(np.ceil(len(ordered) * 0.95)) - 1))
    report["performance"] = {
        "runs": len(ordered),
        "p50_ms": float(ordered[p50_index]),
        "p95_ms": float(ordered[p95_index]),
        "mean_ms": float(sum(ordered) / len(ordered)),
        "process_cpu_seconds": float(time.process_time() - started_cpu),
        "tracemalloc_peak_bytes": int(peak_bytes),
    }

    errors: list[dict[str, Any]] = []
    for row, actual, predicted, scores in zip(test_rows, truth, test_predictions, test_probabilities):
        actual_labels = [label for label, value in zip(label_names, actual) if value]
        predicted_labels = [label for label, value in zip(label_names, predicted) if value]
        if actual_labels != predicted_labels:
            errors.append(
                {
                    "request_id": row.get("request_id"),
                    "language": row.get("language"),
                    "actual_labels": actual_labels,
                    "predicted_labels": predicted_labels,
                    "scores": {label: round(float(score), 6) for label, score in zip(label_names, scores)},
                }
            )
    report["error_analysis"] = {"count": len(errors), "examples": errors[:20]}
    report["model"] = {
        "artifact_dir": str(model_dir),
        "model_type": model.config.model_type,
        "base_model": "FacebookAI/xlm-roberta-base",
        "label_names": list(label_names),
        "max_length": max_length,
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "serialized_size_bytes": sum(path.stat().st_size for path in model_dir.glob("*.safetensors")),
    }
    report["dataset"] = {
        "path": str(dataset_path),
        "version": "invoiceops-v1-dataset-20260915",
        "sha256": sha256_file(dataset_path),
        "train_rows": len(train_rows),
        "dev_rows": len(dev_rows),
        "test_rows": len(test_rows),
    }
    report["environment"] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "llm_enabled": False,
        "runs_requested": runs,
    }
    report["gate"] = {
        "macro_f1_ge_0_80": report["macro_f1"] >= 0.80,
        "micro_f1_ge_0_85": report["micro_f1"] >= 0.85,
        "supplier_master_recall_ge_0_95": report["per_label"]["SUPPLIER_MASTER_CHANGE"]["recall"] >= 0.95,
        "bilingual_macro_delta_le_0_08": (
            "en" in report["language_groups"]
            and "zh" in report["language_groups"]
            and abs(
                report["language_groups"]["en"]["macro_f1"]
                - report["language_groups"]["zh"]["macro_f1"]
            )
            <= 0.08
        ),
        "p95_ms_le_800": report["performance"]["p95_ms"] <= 800,
    }
    report["status"] = "evaluated"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=Path("ml/data/processed/invoiceops-v1-dataset.csv"))
    parser.add_argument("--output", type=Path, default=Path("docs/model/xlmr-evaluation-v1.json"))
    parser.add_argument("--runs", type=int, default=500)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.model_dir, args.dataset, args.output, args.runs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
