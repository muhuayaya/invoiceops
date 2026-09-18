"""Data contract, manifest, and coverage checks for InvoiceOps v1."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

LABELS = (
    "PRICE_VARIANCE",
    "QUANTITY_RECEIPT_VARIANCE",
    "TAX_CURRENCY_AMOUNT",
    "DUPLICATE_INVOICE",
    "MISSING_PO_OR_RECEIPT",
    "PAYMENT_STATUS",
    "SUPPLIER_MASTER_CHANGE",
    "OTHER_REVIEW",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_dataset(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["labels"] = [label for label in row.get("labels", "").split("|") if label]
        row["redaction_types"] = json.loads(row.get("redaction_types", "[]"))
    return rows


def coverage_report(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    label_counts = {label: 0 for label in LABELS}
    combo_counts: dict[str, int] = {}
    language_counts: dict[str, int] = {}
    for row in rows:
        labels = sorted(row.get("labels", []))
        for label in labels:
            label_counts[label] = label_counts.get(label, 0) + 1
        combo = "+".join(labels)
        combo_counts[combo] = combo_counts.get(combo, 0) + 1
        language = str(row.get("language", "unknown"))
        language_counts[language] = language_counts.get(language, 0) + 1
    return {
        "rows": len(rows),
        "label_counts": label_counts,
        "combination_counts": dict(sorted(combo_counts.items())),
        "language_counts": dict(sorted(language_counts.items())),
        "missing_labels": [label for label, count in label_counts.items() if count == 0],
    }


def validate_rows(rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        raise AssertionError("dataset is empty")
    for row in rows:
        labels = row.get("labels", [])
        if not labels or not set(labels).issubset(LABELS):
            raise AssertionError(f"invalid labels for {row.get('request_id')}: {labels}")
        if row.get("taxonomy_version") != "invoiceops-v1":
            raise AssertionError("dataset is not using invoiceops-v1")
        if not row.get("template_group") or not row.get("translation_group"):
            raise AssertionError("template_group and translation_group are required")
        if any(token in str(row.get("text", "")) for token in ("@", "INV-", "PO-")):
            raise AssertionError(f"unredacted identifier in {row.get('request_id')}")
    missing = coverage_report(rows)["missing_labels"]
    if missing:
        raise AssertionError(f"labels have no samples: {missing}")


def validate_manifest(manifest: dict[str, Any], root: Path) -> None:
    required_source_fields = {"source_id", "url", "license", "purpose", "prohibited_use"}
    for source in manifest.get("sources", []):
        missing = required_source_fields - set(source)
        if missing:
            raise AssertionError(f"source {source.get('source_id')} missing {sorted(missing)}")
    for item in manifest.get("input_files", []):
        for field in ("path", "source_id", "sha256", "license", "purpose", "prohibited_use"):
            if not item.get(field):
                raise AssertionError(f"input file missing {field}: {item}")
        path = root / item["path"]
        if not path.is_file():
            raise AssertionError(f"manifest file does not exist: {path}")
        actual = sha256_file(path)
        if actual != item["sha256"]:
            raise AssertionError(f"checksum mismatch for {item['path']}")
