"""Artifact integrity and fallback approval contract."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import joblib

from invoiceops.ml_runtime import ModelLoadError, load_model


LABELS = [
    "DUPLICATE_INVOICE", "MISSING_PO_OR_RECEIPT", "OTHER_REVIEW",
    "PAYMENT_STATUS", "PRICE_VARIANCE", "QUANTITY_RECEIPT_VARIANCE",
    "SUPPLIER_MASTER_CHANGE", "TAX_CURRENCY_AMOUNT",
]


class StubEightOutputModel:
    def predict_proba(self, texts):
        return __import__("numpy").full((len(texts), 8), 0.5)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ModelRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.taxonomy = self.root / "taxonomy.json"
        self.taxonomy.write_text(json.dumps({"taxonomy_version": "invoiceops-v1", "labels": LABELS}))
        model_file = self.root / "model.joblib"
        joblib.dump(StubEightOutputModel(), model_file)
        self.manifest = {
            "model_type": "tfidf", "version": "test-v1", "threshold_version": "test-v1-dev-v1",
            "threshold_model_version": "test-v1", "taxonomy_version": "invoiceops-v1",
            "labels": LABELS, "thresholds": [0.5] * 8,
            "assets": {"model.joblib": sha(model_file)},
        }
        self.write_manifest()

    def write_manifest(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))

    def approve(self):
        (self.root / "approval.json").write_text(json.dumps({
            "engineering_approved": True, "role": "fallback", "model_version": "test-v1",
            "manifest_sha256": sha(self.root / "manifest.json"),
        }))

    def test_unapproved_fallback_is_rejected(self):
        with self.assertRaises(ModelLoadError):
            load_model(self.root, role="fallback", taxonomy_path=self.taxonomy)
        candidate = load_model(self.root, role="fallback", taxonomy_path=self.taxonomy,
                               require_engineering_approval=False)
        self.assertFalse(candidate.engineering_approved)
        self.assertEqual(len(candidate.predict_proba(["text"])[0]), 8)

    def test_explicit_demo_mode_can_serve_unapproved_candidate_without_approval_record(self):
        model = load_model(
            self.root,
            role="fallback",
            taxonomy_path=self.taxonomy,
            allow_unapproved_demo=True,
        )
        self.assertFalse(model.engineering_approved)
        self.assertTrue(model.serving_allowed)
        self.assertTrue(model.demo_mode)
        self.assertEqual(len(model.predict_proba(["sample text"])[0]), 8)

    def test_approved_fallback_and_asset_tamper(self):
        self.approve()
        model = load_model(self.root, role="fallback", taxonomy_path=self.taxonomy)
        self.assertTrue(model.engineering_approved)
        self.assertEqual(model.version, "test-v1")
        with (self.root / "model.joblib").open("ab") as file:
            file.write(b"tamper")
        with self.assertRaises(ModelLoadError):
            load_model(self.root, role="fallback", taxonomy_path=self.taxonomy)

    def test_approval_is_bound_to_manifest_and_role(self):
        self.approve()
        self.manifest["thresholds"][0] = 0.6
        self.write_manifest()
        with self.assertRaises(ModelLoadError):
            load_model(self.root, role="fallback", taxonomy_path=self.taxonomy)
        with self.assertRaises(ModelLoadError):
            load_model(self.root, role="primary", taxonomy_path=self.taxonomy)

    def test_label_order_and_threshold_association(self):
        self.approve()
        self.manifest["labels"] = list(reversed(LABELS))
        self.write_manifest()
        with self.assertRaises(ModelLoadError):
            load_model(self.root, role="fallback", taxonomy_path=self.taxonomy)
        self.manifest["labels"] = LABELS
        self.manifest["threshold_model_version"] = "different"
        self.write_manifest()
        with self.assertRaises(ModelLoadError):
            load_model(self.root, role="fallback", taxonomy_path=self.taxonomy,
                       require_engineering_approval=False)


    def test_tfidf_cannot_be_loaded_as_primary_even_with_primary_approval(self):
        (self.root / "approval.json").write_text(json.dumps({
            "engineering_approved": True, "role": "primary", "model_version": "test-v1",
            "manifest_sha256": sha(self.root / "manifest.json"),
        }))
        with self.assertRaisesRegex(ModelLoadError, "First-release primary must be XLM-R"):
            load_model(self.root, role="primary", taxonomy_path=self.taxonomy)


if __name__ == "__main__":
    unittest.main()
