"""Add a provenance card to an unapproved trained model bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from invoiceops.ml_runtime.runtime import _sha256
from ml.training import _json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base", help="read-only XLM-R pretrained base directory")
    parser.add_argument("--base-revision", help="pinned upstream commit")
    args = parser.parse_args()
    root = Path(args.model)
    if (root / "approval.json").exists():
        parser.error("cannot alter an approved model bundle")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model_type = manifest["model_type"]
    if model_type == "xlmr" and (not args.base or not args.base_revision):
        parser.error("XLM-R requires --base and --base-revision")
    card = {
        "model_version": manifest["version"],
        "model_type": model_type,
        "intended_role": "primary" if model_type == "xlmr" else "fallback_candidate",
        "taxonomy_version": manifest["taxonomy_version"],
        "labels": manifest["labels"],
        "dataset_manifest": manifest["dataset_manifest"],
        "training_config": manifest["config"],
        "threshold_version": manifest["threshold_version"],
        "dev_report_sha256": manifest["assets"]["dev_report.json"],
        "test_evaluated": "test_report.json" in manifest["assets"],
        "engineering_approved": False,
        "real_business_approved": False,
        "data_limitations": "All source labels have pending_review status; offline results are engineering evidence only.",
    }
    if model_type == "xlmr":
        base = Path(args.base)
        card["pretrained_base"] = {
            "upstream_id": "FacebookAI/xlm-roberta-base",
            "upstream_commit": args.base_revision,
            "license": "MIT",
            "license_source": "https://huggingface.co/FacebookAI/xlm-roberta-base",
            "config_sha256": _sha256(base / "config.json"),
            "weights_sha256": _sha256(base / "model.safetensors"),
            "tokenizer_sha256": _sha256(base / "tokenizer.json"),
        }
    _json(root / "model_card.json", card)
    manifest["assets"]["model_card.json"] = _sha256(root / "model_card.json")
    _json(manifest_path, manifest)
    print(json.dumps({"model_version": manifest["version"], "model_card_sha256": manifest["assets"]["model_card.json"]}))


if __name__ == "__main__":
    main()
