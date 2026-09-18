from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class XLMRTrainingConfig:
    base_model: str = "xlm-roberta-base"
    max_length: int = 256
    seed: int = 42
    learning_rate: float = 2e-5
    epochs: int = 3
    batch_size: int = 16


class XLMRDataset:
    """Lazy dataset adapter; imports Transformers only when the feature is used."""

    def __init__(self, rows: list[dict[str, Any]], label_codes: list[str], tokenizer: Any, max_length: int = 256) -> None:
        self.rows = rows
        self.label_codes = label_codes
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        row = self.rows[index]
        encoded = self.tokenizer(
            row["text"],
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        labels = [float(code in row["labels"]) for code in self.label_codes]
        encoded = {key: value.squeeze(0) for key, value in encoded.items()}
        encoded["labels"] = labels
        return encoded


def build_xlmr_model(config: XLMRTrainingConfig, num_labels: int) -> Any:
    """Build a sigmoid multi-label XLM-R head without hiding the dependency."""
    try:
        from transformers import AutoModelForSequenceClassification
    except ImportError as exc:  # pragma: no cover - dependency is optional in unit tests
        raise RuntimeError("transformers is required for XLM-R training") from exc
    return AutoModelForSequenceClassification.from_pretrained(
        config.base_model,
        num_labels=num_labels,
        problem_type="multi_label_classification",
    )


def write_training_config(config: XLMRTrainingConfig, path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                f"base_model: {config.base_model}",
                f"max_length: {config.max_length}",
                f"seed: {config.seed}",
                f"learning_rate: {config.learning_rate}",
                f"epochs: {config.epochs}",
                f"batch_size: {config.batch_size}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
