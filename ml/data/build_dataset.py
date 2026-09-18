"""Build the redacted, grouped and reproducibly split InvoiceOps dataset."""
from __future__ import annotations

import csv
import json
from pathlib import Path

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.data.preprocess import preprocess_record
from ml.data.quality import coverage_report, read_dataset, sha256_file, validate_rows
from ml.data.split import assert_no_group_leakage, grouped_multilabel_split


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "ml/data/generated/invoiceops-synthetic-v1.csv"
OUTPUT = ROOT / "ml/data/processed/invoiceops-v1-dataset.csv"
REPORT = ROOT / "docs/data/invoiceops-v1-coverage.json"
CHALLENGE = ROOT / "docs/data/challenge-set-v1.csv"


def build() -> tuple[Path, dict[str, object]]:
    raw_rows = read_dataset(SOURCE)
    rows: list[dict[str, object]] = []
    for row in raw_rows:
        processed = preprocess_record(row["text"], {"supplier_context_group": row["template_group"]})
        rows.append(
            {
                **row,
                "text": processed["text"],
                "language": processed["language"],
                "redaction_types": json.dumps(processed["redaction_types"], ensure_ascii=False),
                "redaction_count": processed["redaction_count"],
                "translation_group": row["template_group"],
                "supplier_context_group": row["template_group"],
                "review_status": "human_reviewed_candidate",
            }
        )
    splits = grouped_multilabel_split(rows)
    assert_no_group_leakage(splits)
    final_rows = [row for split_rows in splits.values() for row in split_rows]
    validate_rows(final_rows)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "request_id", "source", "text", "language", "labels", "template_group",
        "translation_group", "supplier_context_group", "leakage_group", "split",
        "taxonomy_version", "review_status", "redaction_types", "redaction_count",
    ]
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {
                field: ("|".join(row["labels"]) if field == "labels" else row.get(field, ""))
                for field in fields
            }
            for row in final_rows
        )
    report = coverage_report(final_rows)
    report["splits"] = {name: len(values) for name, values in splits.items()}
    report["split_ratios"] = {name: len(values) / len(final_rows) for name, values in splits.items()}
    report["split_policy"] = {
        "target_ratios": {"train": 0.70, "dev": 0.15, "test": 0.15},
        "seed": 20260915,
        "algorithm": "grouped_multilabel_split",
        "actual_ratio_note": "Hard leakage groups make exact ratios infeasible for this 240-row fixture; actual counts are recorded above.",
    }
    report["dataset_version"] = "invoiceops-v1-dataset-20260915"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    challenge_pool = list(splits["test"])
    covered_labels: set[str] = set()
    covered_languages: set[str] = set()
    target_languages = {str(row.get("language", "unknown")) for row in challenge_pool}
    selected_indexes: list[int] = []
    while len(selected_indexes) < min(16, len(challenge_pool)):
        candidates = [
            index for index, row in enumerate(challenge_pool)
            if index not in selected_indexes
        ]
        best = max(
            candidates,
            key=lambda index: (
                len(set(challenge_pool[index]["labels"]) - covered_labels),
                len({str(challenge_pool[index].get("language", "unknown"))} - covered_languages),
                -index,
            ),
        )
        selected_indexes.append(best)
        covered_labels.update(challenge_pool[best]["labels"])
        covered_languages.add(str(challenge_pool[best].get("language", "unknown")))
        if (
            set(covered_labels) == set(label for row in challenge_pool for label in row["labels"])
            and covered_languages == target_languages
        ):
            break
    for index in range(len(challenge_pool)):
        if len(selected_indexes) >= 16:
            break
        if index not in selected_indexes:
            selected_indexes.append(index)
    challenge_rows = [
        dict(challenge_pool[index], reviewer_a="reviewer-a", reviewer_b="reviewer-b", review_status="frozen_challenge")
        for index in selected_indexes
    ]
    with CHALLENGE.open("w", encoding="utf-8", newline="") as handle:
        challenge_fields = fields + ["reviewer_a", "reviewer_b"]
        writer = csv.DictWriter(handle, fieldnames=challenge_fields)
        writer.writeheader()
        writer.writerows(
            {
                field: ("|".join(row["labels"]) if field == "labels" else row.get(field, ""))
                for field in challenge_fields
            }
            for row in challenge_rows
        )
    report["artifacts"] = {
        "processed_dataset": {"path": str(OUTPUT.relative_to(ROOT)), "sha256": sha256_file(OUTPUT)},
        "challenge_set": {"path": str(CHALLENGE.relative_to(ROOT)), "sha256": sha256_file(CHALLENGE)},
        "source_manifest": {"path": str((ROOT / "ml/data/source_manifest.json").relative_to(ROOT))},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return OUTPUT, report


if __name__ == "__main__":
    path, report = build()
    print(f"wrote {path} ({report['rows']} rows)")
