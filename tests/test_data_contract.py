import csv
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoiceops.data import DataContractError, import_key, load_dataset, load_taxonomy  # noqa: E402
from invoiceops.data.contract import LABELS, REQUIRED_COLUMNS  # noqa: E402


def _row(split, *, request_id="ticket-0001", text=None, labels="PRICE_VARIANCE"):
    return {
        "request_id": request_id,
        "source": "portal",
        "text": text if text is not None else f"Unique {split} invoice text",
        "language": "en",
        "labels": labels,
        "template_group": f"template-{split}",
        "translation_group": f"translation-{split}",
        "supplier_context_group": f"supplier-{split}",
        "leakage_group": f"leakage-{split}",
        "taxonomy_version": "invoiceops-v1",
        "review_status": "pending_review",
        "redaction_types": "[]",
        "redaction_count": "0",
    }


class DataContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = {split: self.root / f"{split}.csv" for split in ("train", "dev", "test")}
        self.rows = {split: [_row(split)] for split in self.paths}
        self._write_all()

    def _write_all(self):
        for split, path in self.paths.items():
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=REQUIRED_COLUMNS)
                writer.writeheader()
                writer.writerows(self.rows[split])

    def _load(self):
        return load_dataset(
            self.paths["train"], self.paths["dev"], self.paths["test"],
            ROOT / "configs" / "taxonomy.json",
        )

    def test_fixed_taxonomy_order_and_text_only_features(self):
        version, labels = load_taxonomy(ROOT / "configs" / "taxonomy.json")
        self.assertEqual(version, "invoiceops-v1")
        self.assertEqual(labels, LABELS)
        self.rows["train"][0]["template_group"] = "PRICE_VARIANCE"
        self._write_all()
        data = self._load()
        self.assertEqual(data.splits["train"].texts, ("Unique train invoice text",))
        self.assertEqual(data.splits["train"].labels[0], tuple(int(x == "PRICE_VARIANCE") for x in LABELS))
        self.assertNotIn("PRICE_VARIANCE", data.splits["train"].texts[0])
        self.assertEqual(data.splits["train"].metadata[0]["template_group"], "PRICE_VARIANCE")

    def test_same_request_id_in_distinct_splits_has_distinct_import_keys(self):
        data = self._load()
        self.assertEqual([data.splits[s].metadata[0]["request_id"] for s in self.paths], ["ticket-0001"] * 3)
        self.assertNotEqual(import_key("invoiceops-v1", "train", "ticket-0001"),
                            import_key("invoiceops-v1", "test", "ticket-0001"))

    def test_manifest_contains_hashes_counts_and_distributions(self):
        data = self._load()
        for split, path in self.paths.items():
            item = data.manifest["splits"][split]
            self.assertEqual(item["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(item["row_count"], 1)
            self.assertEqual(item["label_distribution"]["PRICE_VARIANCE"], 1)
            self.assertEqual(item["language_distribution"], {"en": 1})

    def test_missing_split_or_same_file_is_rejected(self):
        self.paths["test"].unlink()
        with self.assertRaisesRegex(DataContractError, "test .*cannot read"):
            self._load()
        with self.assertRaisesRegex(DataContractError, "three distinct files"):
            load_dataset(self.paths["train"], self.paths["train"], self.paths["dev"], ROOT / "configs" / "taxonomy.json")

    def test_missing_column_empty_text_unknown_and_illegal_labels_are_rejected(self):
        cases = [
            ("text", " ", "empty text"),
            ("labels", "UNRECOGNIZED", "unknown labels"),
            ("labels", "OTHER_REVIEW|PRICE_VARIANCE", "OTHER_REVIEW must stand alone"),
            ("labels", "PRICE_VARIANCE|PRICE_VARIANCE", "invalid label combination"),
            ("taxonomy_version", "other-v1", "taxonomy_version"),
        ]
        for field, value, expected in cases:
            with self.subTest(field=field, value=value):
                self.rows["train"][0][field] = value
                self._write_all()
                with self.assertRaisesRegex(DataContractError, expected) as error:
                    self._load()
                self.assertIn("train.csv:2", str(error.exception))
                self.rows["train"][0] = _row("train")
        with self.paths["train"].open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=REQUIRED_COLUMNS[:-1], extrasaction="ignore")
            writer.writeheader()
            writer.writerow(self.rows["train"][0])
        with self.assertRaisesRegex(DataContractError, "expected columns"):
            self._load()

    def test_cross_split_normalized_text_and_every_group_are_rejected(self):
        for field in ("text", "template_group", "translation_group", "supplier_context_group", "leakage_group"):
            with self.subTest(field=field):
                self.rows["test"][0][field] = (
                    "  UNIQUE   TRAIN INVOICE TEXT  " if field == "text" else self.rows["train"][0][field]
                )
                self._write_all()
                with self.assertRaisesRegex(DataContractError, f"cross-split {field} overlap") as error:
                    self._load()
                self.assertIn("train.csv:2", str(error.exception))
                self.assertIn("test.csv:2", str(error.exception))
                self.rows["test"][0] = _row("test")

    def test_real_files_have_expected_counts(self):
        data = load_dataset(
            ROOT / "data" / "invoiceops_train.csv",
            ROOT / "data" / "invoiceops_dev.csv",
            ROOT / "data" / "invoiceops_test.csv",
            ROOT / "configs" / "taxonomy.json",
        )
        self.assertEqual([data.splits[s].row_count for s in ("train", "dev", "test")], [4372, 781, 847])


if __name__ == "__main__":
    unittest.main()
