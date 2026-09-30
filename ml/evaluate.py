"""One-shot frozen test evaluation after an engineering gate is recorded.

This command is intentionally separate from training and refuses to overwrite a
test report. It does not grant any model approval.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from invoiceops.data import load_dataset
from invoiceops.ml_runtime import load_model
from invoiceops.ml_runtime.runtime import _sha256
from ml.training import _json, _report


_GATE_RULES = {
    "macro_f1": ">=",
    "micro_f1": ">=",
    "supplier_master_change_recall": ">=",
    "zh_en_macro_f1_gap": "<=",
}


def _frozen_gate_for_model(gate: dict, model_version: str) -> tuple[dict, dict]:
    if gate.get("frozen") is not True:
        raise ValueError("frozen gate must set frozen=true")
    if gate.get("scope") != "engineering_quality_only":
        raise ValueError("gate scope must be engineering_quality_only")
    if not gate.get("gate_id"):
        raise ValueError("frozen gate must include gate_id")
    candidate = gate.get("candidate_models", {}).get(model_version)
    if not candidate:
        raise ValueError(f"model version {model_version} is not pinned in the frozen gate")
    criteria = gate.get("criteria")
    if not isinstance(criteria, dict) or set(criteria) != set(_GATE_RULES):
        raise ValueError("frozen gate criteria must contain exactly the four declared quality metrics")
    for metric, expected_operator in _GATE_RULES.items():
        rule = criteria[metric]
        if not isinstance(rule, dict) or rule.get("operator") != expected_operator:
            raise ValueError(f"invalid frozen-gate operator for {metric}")
        threshold = rule.get("threshold")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold):
            raise ValueError(f"invalid frozen-gate threshold for {metric}")
    return candidate, criteria


def _quality_gate_result(report: dict, criteria: dict) -> dict:
    language_slices = report.get("language_slices", {})
    zh_macro = language_slices.get("zh", {}).get("macro_f1")
    en_macro = language_slices.get("en", {}).get("macro_f1")
    measurements = {
        "macro_f1": report.get("macro_f1"),
        "micro_f1": report.get("micro_f1"),
        "supplier_master_change_recall": report.get("supplier_master_change_recall"),
        "zh_en_macro_f1_gap": round(abs(zh_macro - en_macro), 12)
        if isinstance(zh_macro, (int, float)) and isinstance(en_macro, (int, float)) else None,
    }
    checks = {}
    for metric, rule in criteria.items():
        actual = measurements[metric]
        threshold = rule["threshold"]
        passed = actual is not None and math.isfinite(actual) and (
            actual >= threshold if rule["operator"] == ">=" else actual <= threshold
        )
        checks[metric] = {
            "operator": rule["operator"],
            "threshold": threshold,
            "actual": actual,
            "passed": bool(passed),
        }
    all_passed = all(check["passed"] for check in checks.values())
    return {
        "status": "pass_quality_only" if all_passed else "fail_quality",
        "criteria": checks,
        "all_passed": all_passed,
        "engineering_approval_granted": False,
        "real_business_approval_granted": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--train", required=True)
    parser.add_argument("--dev", required=True)
    parser.add_argument("--test", required=True)
    parser.add_argument("--taxonomy", default="configs/taxonomy.json")
    parser.add_argument("--frozen-gate", required=True,
                        help="predeclared engineering quality and latency criteria JSON")
    args = parser.parse_args()
    root = Path(args.model)
    output = root / "test_report.json"
    if output.exists():
        parser.error("test_report.json exists; create a new candidate instead of rerunning test")
    gate_file = Path(args.frozen_gate)
    try:
        gate = json.loads(gate_file.read_text(encoding="utf-8"))
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        candidate, criteria = _frozen_gate_for_model(gate, manifest["version"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        parser.error(f"invalid frozen gate or model manifest: {exc}")
    model_manifest_sha256 = _sha256(manifest_path)
    if candidate.get("manifest_sha256") != model_manifest_sha256:
        parser.error("candidate manifest hash differs from the frozen gate")
    if candidate.get("threshold_version") != manifest.get("threshold_version"):
        parser.error("candidate threshold version differs from the frozen gate")
    if gate.get("dataset_version") != manifest.get("dataset_manifest", {}).get("dataset_version"):
        parser.error("dataset version differs from the frozen gate")
    if gate.get("taxonomy_version") != manifest.get("taxonomy_version"):
        parser.error("taxonomy version differs from the frozen gate")
    expected_role = "primary" if manifest["model_type"] == "xlmr" else "fallback_candidate"
    if candidate.get("role") != expected_role:
        parser.error("candidate role differs from the frozen gate")
    role = "fallback" if manifest["model_type"] == "tfidf" else "primary"
    model = load_model(root, role=role, taxonomy_path=args.taxonomy,
                       require_engineering_approval=False)
    data = load_dataset(args.train, args.dev, args.test, args.taxonomy)
    for split_name in ("train", "dev", "test"):
        expected = manifest["dataset_manifest"]["splits"][split_name]["sha256"]
        if data.splits[split_name].sha256 != expected:
            parser.error(f"{split_name} data hash differs from frozen candidate")
    if gate.get("test_split_sha256") != data.splits["test"].sha256:
        parser.error("test split hash differs from the frozen gate")
    split = data.splits["test"]
    probabilities = np.asarray(model.predict_proba(list(split.texts)))
    report = _report(split, probabilities, list(model.labels), list(model.thresholds))
    quality_gate = _quality_gate_result(report, criteria)
    report.update({"split": "test", "model_version": model.version,
                   "model_type": manifest["model_type"],
                   "candidate_role": candidate.get("role"),
                   "model_manifest_sha256_before_test": model_manifest_sha256,
                   "model_asset_sha256s": manifest["assets"],
                   "gate_sha256": _sha256(gate_file), "data_sha256": split.sha256,
                   "dataset_split_sha256s": {
                       name: data.splits[name].sha256 for name in ("train", "dev", "test")
                   },
                   "threshold_version": model.threshold_version,
                   "frozen_gate_id": gate["gate_id"],
                   "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
                   "evaluator_sha256": _sha256(Path(__file__)),
                   "calibration": "identity; Brier score reported per label",
                   "engineering_quality_gate": quality_gate,
                   "limitations": [
                       "Current source labels are pending_review; this report is engineering evidence only.",
                       "Candidate manifests already contain test row and label-count summaries; no test metrics were used for training or threshold selection.",
                       "Latency and target-environment capacity are evaluated separately.",
                   ]})
    _json(output, report)
    manifest["assets"]["test_report.json"] = _sha256(output)
    card_path = root / "model_card.json"
    if card_path.exists():
        card = json.loads(card_path.read_text(encoding="utf-8"))
        card["test_evaluated"] = True
        card["test_report_sha256"] = _sha256(output)
        _json(card_path, card)
        manifest["assets"]["model_card.json"] = _sha256(card_path)
    manifest["frozen_gate_sha256"] = _sha256(gate_file)
    _json(manifest_path, manifest)
    print(json.dumps({"model_version": model.version, "test_macro_f1": report["macro_f1"],
                      "quality_gate_status": quality_gate["status"],
                      "engineering_approval_granted": False,
                      "report": str(output)}))


if __name__ == "__main__":
    main()
