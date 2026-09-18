"""Reproducible XLM-R multi-label training entry point.

The command intentionally fails clearly when the optional transformer stack or
model weights are unavailable. It never writes a fake model card or fake score.
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import random
from pathlib import Path

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.data.quality import sha256_file
from invoiceops.ml_runtime.xlmr import XLMRDataset, XLMRTrainingConfig, build_xlmr_model, write_training_config


def seed_everything(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np
        import torch

        np.random.seed(seed)
        torch.manual_seed(seed)
    except ImportError as exc:
        raise RuntimeError("torch and numpy are required for XLM-R training") from exc


def load_rows(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["labels"] = [value for value in str(row["labels"]).split("|") if value]
    return rows


def train(config: XLMRTrainingConfig, dataset: Path, output_dir: Path) -> dict[str, object]:
    seed_everything(config.seed)
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError("transformers is required for XLM-R training") from exc
    rows = load_rows(dataset)
    labels = sorted({label for row in rows for label in row["labels"]})
    tokenizer = AutoTokenizer.from_pretrained(config.base_model)
    model = build_xlmr_model(config, len(labels))
    import torch
    from torch.utils.data import DataLoader

    train_rows = [row for row in rows if row.get("split") == "train"] or rows
    train_dataset = XLMRDataset(train_rows, labels, tokenizer, config.max_length)
    loader = DataLoader(train_dataset, batch_size=config.batch_size, shuffle=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
    positives = torch.tensor([sum(label in row["labels"] for row in train_rows) for label in labels], dtype=torch.float32)
    negatives = torch.tensor([len(train_rows) - value for value in positives], dtype=torch.float32)
    loss_function = torch.nn.BCEWithLogitsLoss(pos_weight=negatives / positives.clamp_min(1.0))
    model.train()
    epoch_losses: list[float] = []
    for _epoch in range(config.epochs):
        total_loss = 0.0
        for batch in loader:
            optimizer.zero_grad()
            labels_batch = batch["labels"]
            if isinstance(labels_batch, list):
                labels_batch = torch.stack(labels_batch, dim=1)
            outputs = model(
                input_ids=batch["input_ids"],
                attention_mask=batch.get("attention_mask"),
            )
            loss = loss_function(outputs.logits, torch.as_tensor(labels_batch, dtype=torch.float32))
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach())
        epoch_losses.append(total_loss / max(len(loader), 1))
    output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    write_training_config(config, output_dir / "training-config.yaml")
    record = {
        "status": "trained",
        "base_model": config.base_model,
        "dataset": str(dataset),
        "dataset_version": "invoiceops-v1-dataset-20260915",
        "dataset_sha256": sha256_file(dataset),
        "split_counts": {name: sum(row.get("split") == name for row in rows) for name in ("train", "dev", "test")},
        "taxonomy_version": "invoiceops-v1",
        "seed": config.seed,
        "label_count": len(labels),
        "labels": labels,
        "epochs": config.epochs,
        "epoch_losses": epoch_losses,
        "environment": {"platform": platform.platform(), "python": platform.python_version(), "torch": torch.__version__},
        "model_source_manifest": "docs/model/model-source-manifest-v1.json",
        "warning": "Training loss is not a promotion metric; run frozen evaluation before promotion.",
    }
    (output_dir / "training-record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("ml/data/processed/invoiceops-v1-dataset.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("ml/artifacts/xlmr-v1"))
    parser.add_argument("--base-model", default="xlm-roberta-base")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-length", type=int, default=256)
    args = parser.parse_args()
    config = XLMRTrainingConfig(
        base_model=args.base_model,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        batch_size=args.batch_size,
        max_length=args.max_length,
    )
    print(json.dumps(train(config, args.dataset, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
