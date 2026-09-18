"""TF-IDF + One-vs-Rest logistic-regression baseline for InvoiceOps v1."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier

from ml.data.quality import LABELS
from ml.evaluation.metrics import (
    benchmark_latency,
    classification_metrics,
    evaluate_by_language,
    serialized_size_bytes,
)


class TfidfOneVsRestBaseline:
    def __init__(self, label_names: Sequence[str] = LABELS) -> None:
        self.label_names = tuple(label_names)
        self.vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            min_df=1,
            sublinear_tf=True,
        )
        self.classifier = OneVsRestClassifier(
            LogisticRegression(max_iter=500, random_state=20260915)
        )
        self._fitted = False

    def fit(self, texts: Sequence[str], labels: Sequence[Sequence[str]]) -> "TfidfOneVsRestBaseline":
        matrix = self.vectorizer.fit_transform(texts)
        encoded = np.asarray([[int(label in row) for label in self.label_names] for row in labels])
        self.classifier.fit(matrix, encoded)
        self._fitted = True
        return self

    def predict_proba(self, texts: Sequence[str]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("baseline must be fitted before prediction")
        return np.asarray(self.classifier.predict_proba(self.vectorizer.transform(texts)))

    def predict(self, texts: Sequence[str], threshold: float = 0.5) -> np.ndarray:
        return (self.predict_proba(texts) >= threshold).astype(int)

    def predict_one(self, text: str, threshold: float = 0.5) -> dict[str, float]:
        probabilities = self.predict_proba([text])[0]
        return {label: float(probabilities[index]) for index, label in enumerate(self.label_names) if probabilities[index] >= threshold}


def _load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["labels"] = [label for label in row["labels"].split("|") if label]
    return rows


def train_and_evaluate(dataset_path: Path) -> tuple[TfidfOneVsRestBaseline, dict[str, Any]]:
    rows = _load_rows(dataset_path)
    splits = {name: [row for row in rows if row.get("split") == name] for name in ("train", "dev", "test")}
    if any(not split_rows for split_rows in splits.values()):
        raise ValueError("dataset must contain train, dev, and test rows")
    model = TfidfOneVsRestBaseline().fit(
        [row["text"] for row in splits["train"]],
        [row["labels"] for row in splits["train"]],
    )
    test_rows = splits["test"]
    y_true = np.asarray([[int(label in row["labels"]) for label in LABELS] for row in test_rows])
    y_pred = model.predict([row["text"] for row in test_rows])
    report = classification_metrics(y_true, y_pred, LABELS)
    report["language_groups"] = evaluate_by_language(test_rows, y_true, y_pred, LABELS)
    report["latency"] = benchmark_latency(model.predict_one, [row["text"] for row in test_rows])
    report["model_size_bytes"] = serialized_size_bytes(model.vectorizer, model.classifier)
    report["model"] = {
        "name": "tfidf-char-wb+one-vs-rest-logistic-regression",
        "feature": {"analyzer": "char_wb", "ngram_range": [3, 5], "sublinear_tf": True},
        "taxonomy_version": "invoiceops-v1",
        "dataset_path": str(dataset_path),
        "dataset_scope": "synthetic PoC candidates; not real enterprise data",
    }
    return model, report


def write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
