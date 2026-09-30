"""Train and evaluate the two fixed eight-label models.

Training commands validate all three splits but never evaluate on test. Use the
separate evaluate-test command only after engineering gates are frozen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from pathlib import Path

import numpy as np

from invoiceops.data import load_dataset
from invoiceops.ml_runtime.runtime import _sha256


def _json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load(args):
    return load_dataset(args.train, args.dev, args.test, args.taxonomy)


def _labels(args):
    return json.loads(Path(args.taxonomy).read_text(encoding="utf-8"))


def _seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        torch.use_deterministic_algorithms(True)
        torch.backends.cudnn.benchmark = False
    except ImportError:
        pass


def _thresholds(y: np.ndarray, p: np.ndarray) -> list[float]:
    from sklearn.metrics import f1_score

    candidates = np.arange(0.10, 0.901, 0.025)
    chosen = []
    for index in range(y.shape[1]):
        scores = [(float(f1_score(y[:, index], p[:, index] >= threshold, zero_division=0)),
                   -abs(float(threshold) - 0.5), float(threshold)) for threshold in candidates]
        chosen.append(round(max(scores)[2], 3))
    return chosen


def _report(split, probabilities: np.ndarray, labels: list[str], thresholds: list[float], *, samples: int = 200) -> dict:
    from sklearn.metrics import (
        accuracy_score, average_precision_score, brier_score_loss,
        f1_score, precision_recall_fscore_support,
    )

    truth = np.asarray(split.labels, dtype=int)
    predicted = (probabilities >= np.asarray(thresholds)).astype(int)
    precision, recall, f1, support = precision_recall_fscore_support(truth, predicted, average=None, zero_division=0)
    metrics = {
        "rows": int(len(truth)),
        "micro_f1": float(f1_score(truth, predicted, average="micro", zero_division=0)),
        "macro_f1": float(f1_score(truth, predicted, average="macro", zero_division=0)),
        "subset_accuracy": float(accuracy_score(truth, predicted)),
        "per_label": {
            label: {
                "precision": float(precision[i]), "recall": float(recall[i]),
                "f1": float(f1[i]), "support": int(support[i]),
                "pr_auc": float(average_precision_score(truth[:, i], probabilities[:, i])) if len(set(truth[:, i])) == 2 else None,
                "brier": float(brier_score_loss(truth[:, i], probabilities[:, i])),
            }
            for i, label in enumerate(labels)
        },
    }
    metrics["supplier_master_change_recall"] = metrics["per_label"]["SUPPLIER_MASTER_CHANGE"]["recall"]
    for field in ("language", "source"):
        slices = {}
        for value in sorted({row[field] for row in split.metadata}):
            indices = [i for i, row in enumerate(split.metadata) if row[field] == value]
            slices[value] = {
                "rows": len(indices),
                "macro_f1": float(f1_score(truth[indices], predicted[indices], average="macro", zero_division=0)),
                "micro_f1": float(f1_score(truth[indices], predicted[indices], average="micro", zero_division=0)),
                "per_label_support": {
                    label: int(truth[indices, label_index].sum())
                    for label_index, label in enumerate(labels)
                },
            }
        metrics[field + "_slices"] = slices
    mistakes = np.flatnonzero(np.any(predicted != truth, axis=1))[:20]
    metrics["errors"] = [
        {"request_id": split.metadata[i]["request_id"],
         "language": split.metadata[i]["language"],
         "true": [labels[j] for j in np.flatnonzero(truth[i])],
         "predicted": [labels[j] for j in np.flatnonzero(predicted[i])]}
        for i in mistakes
    ]
    rng = np.random.default_rng(20260925)
    boot = []
    for _ in range(samples):
        indices = rng.integers(0, len(truth), len(truth))
        boot.append(f1_score(truth[indices], predicted[indices], average="macro", zero_division=0))
    metrics["macro_f1_ci95_bootstrap"] = [float(x) for x in np.quantile(boot, [0.025, 0.975])]
    return metrics


def _versions(model_type: str, args, train_hash: str, dev_hash: str, taxonomy: dict,
              training_device: str | None = None) -> str:
    import sklearn

    dependencies = {"numpy": np.__version__, "scikit-learn": sklearn.__version__}
    if model_type == "xlmr":
        import torch
        import transformers

        dependencies.update({"torch": torch.__version__, "transformers": transformers.__version__})
    config = {
        "model_type": model_type, "seed": args.seed, "train_hash": train_hash,
        "dev_hash": dev_hash, "taxonomy": taxonomy, "dependencies": dependencies,
        "training_code_sha256": _sha256(Path(__file__)),
    }
    if model_type == "tfidf":
        config.update({"max_features": args.max_features, "c": args.c,
                       "vectorizer": "char_wb 3-5 sublinear_tf float32",
                       "classifier": "OneVsRest LogisticRegression max_iter=1000"})
    else:
        config.update({"revision": args.base_revision, "max_length": args.max_length,
                       "epochs": args.epochs, "learning_rate": args.learning_rate,
                       "batch_size": args.batch_size,
                       "gradient_accumulation": args.gradient_accumulation,
                       "deterministic_algorithms": True})
        config["training_device"] = training_device
    suffix = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
    return f"{model_type}-{suffix}"


def _manifest(output: Path, *, model_type: str, version: str, dataset, taxonomy: dict,
              thresholds: list[float], config: dict, assets: list[Path], max_length: int | None = None) -> None:
    import sklearn

    manifest = {
        "model_type": model_type,
        "version": version,
        "threshold_version": version + "-dev-v1",
        "threshold_model_version": version,
        "taxonomy_version": taxonomy["taxonomy_version"],
        "labels": taxonomy["labels"],
        "thresholds": thresholds,
        "dataset_manifest": dataset.manifest,
        "config": config,
        "dependencies": {"numpy": np.__version__, "scikit-learn": sklearn.__version__},
        "assets": {str(path.relative_to(output)).replace("\\", "/"): _sha256(path) for path in assets},
    }
    if max_length is not None:
        import torch
        import transformers

        manifest["max_length"] = max_length
        manifest["dependencies"].update({"torch": torch.__version__, "transformers": transformers.__version__})
    _json(output / "manifest.json", manifest)


def train_tfidf(args) -> None:
    import joblib
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.multiclass import OneVsRestClassifier
    from sklearn.pipeline import make_pipeline

    _seed(args.seed)
    data = _load(args)
    taxonomy = _labels(args)
    train, dev = data.splits["train"], data.splits["dev"]
    model = make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=args.max_features,
                        sublinear_tf=True, dtype=np.float32),
        OneVsRestClassifier(LogisticRegression(C=args.c, max_iter=1000, random_state=args.seed)),
    )
    model.fit(train.texts, np.asarray(train.labels, dtype=int))
    probabilities = model.predict_proba(dev.texts)
    thresholds = _thresholds(np.asarray(dev.labels), probabilities)
    version = _versions("tfidf", args, train.sha256, dev.sha256, taxonomy)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    model_file = output / "model.joblib"
    joblib.dump(model, model_file, compress=3)
    report = _report(dev, probabilities, taxonomy["labels"], thresholds)
    report.update({"split": "dev", "model_version": version, "data_sha256": dev.sha256,
                   "calibration": "identity; Brier score reported per label"})
    _json(output / "dev_report.json", report)
    config = {"seed": args.seed, "vectorizer": "char_wb 3-5", "max_features": args.max_features,
              "c": args.c, "training_code_sha256": _sha256(Path(__file__)), "fit_split": "train", "threshold_split": "dev"}
    _manifest(output, model_type="tfidf", version=version, dataset=data, taxonomy=taxonomy,
              thresholds=thresholds, config=config, assets=[model_file, output / "dev_report.json"])
    print(json.dumps({"version": version, "dev_macro_f1": report["macro_f1"], "output": str(output)}))


def tokenizer_stats(args) -> None:
    from transformers import AutoTokenizer

    data = _load(args)
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, revision=args.base_revision, local_files_only=args.offline)
    lengths = [len(ids) for ids in tokenizer(list(data.splits["train"].texts), add_special_tokens=True,
                                              truncation=False)["input_ids"]]
    report = {"base_model": args.base_model, "base_revision": args.base_revision,
              "train_sha256": data.splits["train"].sha256, "rows": len(lengths),
              "length_percentiles": {str(q): float(np.percentile(lengths, q)) for q in (50, 90, 95, 99, 100)},
              "truncation_rate": {str(n): float(np.mean(np.asarray(lengths) > n)) for n in (128, 192, 256, 384, 512)},
              "selected_max_length": args.max_length}
    _json(Path(args.output), report)
    print(json.dumps(report))


def train_xlmr(args) -> None:
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    import torch
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    if args.cpu and args.device != "auto":
        raise ValueError("--cpu cannot be combined with --device; use --device cpu instead")
    requested_device = "cpu" if args.cpu else args.device
    cuda_available = torch.cuda.is_available()
    if requested_device == "cuda" and not cuda_available:
        raise RuntimeError("CUDA was requested but is unavailable in this PyTorch environment")
    device = "cuda" if requested_device == "cuda" or (requested_device == "auto" and cuda_available) else "cpu"
    hardware = {
        "requested_device": requested_device,
        "training_device": device,
        "torch_version": torch.__version__,
        "cuda_runtime_version": torch.version.cuda,
        "gpu_name": torch.cuda.get_device_name(0) if device == "cuda" else None,
    }
    if device == "cuda":
        hardware["gpu_total_memory_bytes"] = torch.cuda.get_device_properties(0).total_memory
    print(json.dumps({"training_environment": hardware}), flush=True)
    _seed(args.seed)
    data = _load(args)
    taxonomy = _labels(args)
    train, dev = data.splits["train"], data.splits["dev"]
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, revision=args.base_revision, local_files_only=args.offline)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base_model, revision=args.base_revision, local_files_only=args.offline,
        num_labels=8, problem_type="multi_label_classification",
    )
    model.to(device)

    class TextDataset(Dataset):
        def __init__(self, split):
            self.texts, self.labels = split.texts, split.labels

        def __len__(self):
            return len(self.texts)

        def __getitem__(self, index):
            return self.texts[index], self.labels[index]

    def collate(batch):
        texts, labels = zip(*batch)
        encoded = tokenizer(list(texts), padding=True, truncation=True,
                            max_length=args.max_length, return_tensors="pt")
        encoded["labels"] = torch.tensor(np.asarray(labels), dtype=torch.float32)
        return encoded

    train_loader = DataLoader(TextDataset(train), batch_size=args.batch_size, shuffle=True,
                              generator=torch.Generator().manual_seed(args.seed), collate_fn=collate)
    dev_loader = DataLoader(TextDataset(dev), batch_size=args.batch_size, collate_fn=collate)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    best = -1.0
    history = []
    training_started = time.perf_counter()
    for epoch in range(args.epochs):
        epoch_started = time.perf_counter()
        model.train()
        loss_sum = 0.0
        optimizer.zero_grad(set_to_none=True)
        for step, batch in enumerate(train_loader):
            batch = {key: value.to(device) for key, value in batch.items()}
            loss = model(**batch).loss / args.gradient_accumulation
            loss.backward()
            loss_sum += float(loss.detach()) * args.gradient_accumulation
            if (step + 1) % args.gradient_accumulation == 0 or step + 1 == len(train_loader):
                optimizer.step()
                optimizer.zero_grad(set_to_none=True)
        model.eval()
        probabilities = []
        with torch.inference_mode():
            for batch in dev_loader:
                batch = {key: value.to(device) for key, value in batch.items()}
                probabilities.extend(torch.sigmoid(model(**batch).logits).cpu().tolist())
        probabilities = np.asarray(probabilities)
        from sklearn.metrics import f1_score

        score = float(f1_score(np.asarray(dev.labels), probabilities >= 0.5, average="macro", zero_division=0))
        history.append({"epoch": epoch + 1, "train_loss_sum": loss_sum,
                        "dev_macro_f1_at_0_5": score,
                        "duration_seconds": round(time.perf_counter() - epoch_started, 3)})
        print(json.dumps(history[-1]), flush=True)
        if score > best:
            best = score
            model.save_pretrained(output / "model", safe_serialization=True)
            tokenizer.save_pretrained(output / "model")
    training_duration = round(time.perf_counter() - training_started, 3)
    if device == "cuda":
        hardware["gpu_peak_allocated_bytes"] = torch.cuda.max_memory_allocated()
    _json(output / "train_log.json", {
        "training_environment": hardware,
        "training_duration_seconds": training_duration,
        "checkpoint_metric": "dev_macro_f1_at_0_5",
        "history": history,
    })
    del model
    if device == "cuda":
        torch.cuda.empty_cache()
    model = AutoModelForSequenceClassification.from_pretrained(output / "model", local_files_only=True).to(device).eval()
    probabilities = []
    with torch.inference_mode():
        for batch in dev_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            probabilities.extend(torch.sigmoid(model(**batch).logits).cpu().tolist())
    probabilities = np.asarray(probabilities)
    thresholds = _thresholds(np.asarray(dev.labels), probabilities)
    version = _versions("xlmr", args, train.sha256, dev.sha256, taxonomy, training_device=device)
    report = _report(dev, probabilities, taxonomy["labels"], thresholds)
    report.update({"split": "dev", "model_version": version, "data_sha256": dev.sha256,
                   "calibration": "identity; Brier score reported per label"})
    _json(output / "dev_report.json", report)
    assets = [path for path in (output / "model").rglob("*") if path.is_file()]
    assets.extend([output / "train_log.json", output / "dev_report.json"])
    config = {"seed": args.seed, "base_model": args.base_model, "base_revision": args.base_revision,
              "base_license": args.base_license, "max_length": args.max_length,
              "batch_size": args.batch_size, "gradient_accumulation": args.gradient_accumulation,
              "learning_rate": args.learning_rate, "epochs": args.epochs,
              "training_device": device, "training_device_name": hardware["gpu_name"],
              "training_code_sha256": _sha256(Path(__file__)), "deterministic_algorithms": True,
              "fit_split": "train", "checkpoint_metric": "dev_macro_f1_at_0_5", "threshold_split": "dev"}
    if device == "cuda":
        config["cuda_runtime_version"] = torch.version.cuda
    _manifest(output, model_type="xlmr", version=version, dataset=data, taxonomy=taxonomy,
              thresholds=thresholds, config=config, assets=assets, max_length=args.max_length)
    print(json.dumps({"version": version, "dev_macro_f1": report["macro_f1"], "output": str(output)}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("train-tfidf", "tokenizer-stats", "train-xlmr"):
        command = sub.add_parser(name)
        command.add_argument("--train", required=True)
        command.add_argument("--dev", required=True)
        command.add_argument("--test", required=True)
        command.add_argument("--taxonomy", default="configs/taxonomy.json")
        command.add_argument("--output", required=True)
        command.add_argument("--seed", type=int, default=42)
        if name == "train-tfidf":
            command.add_argument("--max-features", type=int, default=250000)
            command.add_argument("--c", type=float, default=4.0)
        else:
            command.add_argument("--base-model", default="FacebookAI/xlm-roberta-base")
            command.add_argument("--base-revision", required=True)
            command.add_argument("--max-length", type=int, default=256)
            command.add_argument("--offline", action="store_true")
            if name == "train-xlmr":
                command.add_argument("--base-license", required=True)
                command.add_argument("--epochs", type=int, default=3)
                command.add_argument("--batch-size", type=int, default=8)
                command.add_argument("--gradient-accumulation", type=int, default=2)
                command.add_argument("--learning-rate", type=float, default=2e-5)
                command.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
                command.add_argument("--cpu", action="store_true", help="Deprecated alias for --device cpu")
    args = parser.parse_args()
    {"train-tfidf": train_tfidf, "tokenizer-stats": tokenizer_stats, "train-xlmr": train_xlmr}[args.command](args)


if __name__ == "__main__":
    main()
