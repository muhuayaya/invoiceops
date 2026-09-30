"""Strict, read-only contract for the three fixed offline data splits."""

from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LABELS = (
    "DUPLICATE_INVOICE",
    "MISSING_PO_OR_RECEIPT",
    "OTHER_REVIEW",
    "PAYMENT_STATUS",
    "PRICE_VARIANCE",
    "QUANTITY_RECEIPT_VARIANCE",
    "SUPPLIER_MASTER_CHANGE",
    "TAX_CURRENCY_AMOUNT",
)
TAXONOMY_VERSION = 'invoiceops-v1'
REQUIRED_COLUMNS = (
    "request_id",
    "source",
    "text",
    "language",
    "labels",
    "template_group",
    "translation_group",
    "supplier_context_group",
    "leakage_group",
    "taxonomy_version",
    "review_status",
    "redaction_types",
    "redaction_count",
)
GROUP_COLUMNS = (
    "template_group",
    "translation_group",
    "supplier_context_group",
    "leakage_group",
)
SPLITS = ("train", "dev", "test")


class DataContractError(ValueError):
    """The source data violate the frozen offline data contract."""


@dataclass(frozen=True)
class SplitData:
    texts: tuple[str, ...]
    labels: tuple[tuple[int, ...], ...]
    metadata: tuple[dict[str, str], ...]
    import_keys: tuple[tuple[str, str, str], ...]
    sha256: str
    row_count: int


@dataclass(frozen=True)
class LoadedDataset:
    splits: dict[str, SplitData]
    manifest: dict[str, Any]
    taxonomy_version: str
    label_names: tuple[str, ...]


def load_taxonomy(path: str | Path = "configs/taxonomy.json") -> tuple[str, tuple[str, ...]]:
    path = Path(path)
    try:
        taxonomy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DataContractError(f"taxonomy {path}: cannot read valid UTF-8 JSON: {exc}") from exc
    version = taxonomy.get("taxonomy_version")
    labels = taxonomy.get("labels")
    if version != TAXONOMY_VERSION or labels != list(LABELS):
        raise DataContractError(f"taxonomy {path}: expected version and fixed eight-label order")
    return version, LABELS


def import_key(dataset_version: str, split: str, request_id: str) -> tuple[str, str, str]:
    """Composite primary key for offline imports; never use request_id alone."""
    if not dataset_version or split not in SPLITS or not request_id:
        raise DataContractError("import key needs dataset_version, train/dev/test split and request_id")
    return dataset_version, split, request_id


def _text_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _read_split(path: Path, split: str, taxonomy_version: str, dataset_version: str) -> tuple[SplitData, dict[str, Any], list[tuple[int, dict[str, str]]]]:
    try:
        raw = path.read_bytes()
        decoded = raw.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise DataContractError(f"{split} {path}: cannot read UTF-8 CSV: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    reader = csv.DictReader(decoded.splitlines(keepends=True), strict=True)
    header = reader.fieldnames
    if header is None or len(header) != len(set(header)) or set(header) != set(REQUIRED_COLUMNS):
        raise DataContractError(f"{split} {path}: expected columns {', '.join(REQUIRED_COLUMNS)}; got {header}")

    texts: list[str] = []
    targets: list[tuple[int, ...]] = []
    metadata: list[dict[str, str]] = []
    import_keys: list[tuple[str, str, str]] = []
    indexed_rows: list[tuple[int, dict[str, str]]] = []
    label_counts: Counter[str] = Counter()
    language_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()
    try:
        for row in reader:
            line = reader.line_num
            if None in row or any(value is None for value in row.values()):
                raise DataContractError(f"{split} {path}:{line}: CSV row has wrong column count")
            for column in REQUIRED_COLUMNS:
                if not row[column].strip():
                    raise DataContractError(f"{split} {path}:{line}: empty {column}")
            if row["taxonomy_version"] != taxonomy_version:
                raise DataContractError(f"{split} {path}:{line}: taxonomy_version {row['taxonomy_version']!r} != {taxonomy_version!r}")
            raw_labels = row["labels"].split("|")
            if not 1 <= len(raw_labels) <= 2 or len(set(raw_labels)) != len(raw_labels):
                raise DataContractError(f"{split} {path}:{line}: invalid label combination {row['labels']!r}")
            unknown = set(raw_labels) - set(LABELS)
            if unknown:
                raise DataContractError(f"{split} {path}:{line}: unknown labels {sorted(unknown)}")
            if "OTHER_REVIEW" in raw_labels and len(raw_labels) > 1:
                raise DataContractError(f"{split} {path}:{line}: OTHER_REVIEW must stand alone")
            key = import_key(dataset_version, split, row["request_id"])
            if key[2] in seen_ids:
                raise DataContractError(f"{split} {path}:{line}: duplicate request_id {key[2]!r} within split")
            seen_ids.add(key[2])
            import_keys.append(key)
            texts.append(row["text"])
            targets.append(tuple(int(label in raw_labels) for label in LABELS))
            metadata.append({column: row[column] for column in REQUIRED_COLUMNS if column not in ("text", "labels")})
            indexed_rows.append((line, row))
            label_counts.update(raw_labels)
            language_counts.update([row["language"]])
    except csv.Error as exc:
        raise DataContractError(f"{split} {path}:{reader.line_num}: malformed CSV: {exc}") from exc
    if not texts:
        raise DataContractError(f"{split} {path}: empty split")

    split_data = SplitData(tuple(texts), tuple(targets), tuple(metadata), tuple(import_keys), digest, len(texts))
    summary = {
        "path": str(path.resolve()),
        "sha256": digest,
        "row_count": len(texts),
        "label_distribution": {label: label_counts[label] for label in LABELS},
        "language_distribution": dict(sorted(language_counts.items())),
    }
    return split_data, summary, indexed_rows


def _check_leakage(rows_by_split: dict[str, list[tuple[int, dict[str, str]]]], paths: dict[str, Path]) -> None:
    for field in ("text", *GROUP_COLUMNS):
        seen: dict[str, tuple[str, int, str]] = {}
        for split in SPLITS:
            for line, row in rows_by_split[split]:
                value = _text_key(row[field]) if field == "text" else row[field].strip()
                previous = seen.get(value)
                if previous and previous[0] != split:
                    prior_split, prior_line, prior_id = previous
                    raise DataContractError(
                        f"cross-split {field} overlap {value!r}: "
                        f"{prior_split} {paths[prior_split]}:{prior_line} {prior_id}, "
                        f"{split} {paths[split]}:{line} {row['request_id']}"
                    )
                seen.setdefault(value, (split, line, row["request_id"]))


def load_dataset(
    train_path: str | Path,
    dev_path: str | Path,
    test_path: str | Path,
    taxonomy_path: str | Path = "configs/taxonomy.json",
    *,
    dataset_version: str = "invoiceops-v1",
) -> LoadedDataset:
    """Read all fixed splits; no random splitting and no training side effects."""
    if not dataset_version.strip():
        raise DataContractError("dataset_version must be non-empty")
    taxonomy_version, labels = load_taxonomy(taxonomy_path)
    paths = {split: Path(path) for split, path in zip(SPLITS, (train_path, dev_path, test_path))}
    if len({path.resolve() for path in paths.values()}) != 3:
        raise DataContractError("train, dev and test must be three distinct files")
    splits: dict[str, SplitData] = {}
    summaries: dict[str, dict[str, Any]] = {}
    rows_by_split: dict[str, list[tuple[int, dict[str, str]]]] = {}
    for split in SPLITS:
        splits[split], summaries[split], rows_by_split[split] = _read_split(paths[split], split, taxonomy_version, dataset_version)
    _check_leakage(rows_by_split, paths)
    manifest = {
        "dataset_version": dataset_version,
        "taxonomy_version": taxonomy_version,
        "labels": list(labels),
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "splits": summaries,
    }
    return LoadedDataset(splits, manifest, taxonomy_version, labels)


def validate_dataset(
    train_path: str | Path,
    dev_path: str | Path,
    test_path: str | Path,
    taxonomy_path: str | Path = "configs/taxonomy.json",
    *,
    dataset_version: str = "invoiceops-v1",
) -> dict[str, Any]:
    """Validate three splits and return their SHA-256 manifest."""
    return load_dataset(train_path, dev_path, test_path, taxonomy_path, dataset_version=dataset_version).manifest
