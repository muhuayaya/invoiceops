from pathlib import Path

from ml.data.build_dataset import build
import json

from ml.data.quality import LABELS, read_dataset, validate_manifest, validate_rows


def test_dataset_builder_outputs_valid_split_and_coverage() -> None:
    path, report = build()
    rows = read_dataset(path)
    validate_rows(rows)
    assert report["missing_labels"] == []
    assert set(report["splits"]) == {"train", "dev", "test"}
    for split in ("train", "dev", "test"):
        split_labels = {label for row in rows if row["split"] == split for label in row["labels"]}
        assert set(LABELS) <= split_labels
        assert {row["language"] for row in rows if row["split"] == split} == {"en", "zh", "mixed"}
    challenge_rows = read_dataset(Path(__file__).parents[2] / "docs/data/challenge-set-v1.csv")
    challenge_labels = {label for row in challenge_rows for label in row["labels"]}
    assert set(LABELS) <= challenge_labels
    assert {row["language"] for row in challenge_rows} == {"en", "zh", "mixed"}


def test_source_manifest_has_valid_checksums() -> None:
    root = Path(__file__).parents[2]
    manifest = json.loads((root / "ml/data/source_manifest.json").read_text(encoding="utf-8"))
    validate_manifest(manifest, root)
