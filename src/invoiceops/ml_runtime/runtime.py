"""Load only complete model bundles with matching taxonomy and file hashes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class ModelLoadError(RuntimeError):
    """The model bundle is missing, inconsistent, or unapproved."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelLoadError(f"Invalid metadata: {path}") from exc


@dataclass
class ModelRuntime:
    model: object
    tokenizer: object | None
    model_type: str
    labels: tuple[str, ...]
    thresholds: tuple[float, ...]
    version: str
    threshold_version: str
    engineering_approved: bool = False
    serving_allowed: bool = False
    demo_mode: bool = False
    max_length: int | None = None
    device: str = "cpu"

    def predict_proba(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]:
        if not isinstance(texts, (list, tuple)) or any(not isinstance(t, str) for t in texts):
            raise ValueError("texts must be a sequence of strings")
        if not texts:
            return []
        if self.model_type == "tfidf":
            result = self.model.predict_proba(texts)
            probabilities = result.tolist()
        else:
            import torch

            self.model.eval()
            probabilities = []
            with torch.inference_mode():
                for start in range(0, len(texts), 16):
                    encoded = self.tokenizer(
                        texts[start : start + 16], padding=True, truncation=True,
                        max_length=self.max_length, return_tensors="pt",
                    )
                    encoded = {key: value.to(self.device) for key, value in encoded.items()}
                    logits = self.model(**encoded).logits
                    probabilities.extend(torch.sigmoid(logits).cpu().tolist())
        if any(len(row) != len(self.labels) or any(not 0 <= float(p) <= 1 for p in row) for row in probabilities):
            raise ModelLoadError("Model output does not match eight-label probability contract")
        return probabilities


def load_model(
    path: str | Path,
    *,
    role: str,
    require_engineering_approval: bool = True,
    allow_unapproved_demo: bool = False,
    taxonomy_path: str | Path = "configs/taxonomy.json",
    device: str = "cpu",
) -> ModelRuntime:
    """Verify and load a primary or fallback bundle; approval is role-specific."""
    if role not in {"primary", "fallback"}:
        raise ValueError("role must be primary or fallback")
    root = Path(path)
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path)
    taxonomy = _read_json(Path(taxonomy_path))
    labels = tuple(manifest.get("labels", []))
    if len(labels) != 8 or labels != tuple(taxonomy.get("labels", [])):
        raise ModelLoadError("Label order differs from current taxonomy")
    if manifest.get("taxonomy_version") != taxonomy.get("taxonomy_version"):
        raise ModelLoadError("Taxonomy version differs from current taxonomy")
    thresholds = manifest.get("thresholds", [])
    if len(thresholds) != 8 or any(not isinstance(x, (int, float)) or not 0 <= x <= 1 for x in thresholds):
        raise ModelLoadError("Invalid thresholds")
    if manifest.get("threshold_model_version") != manifest.get("version"):
        raise ModelLoadError("Thresholds belong to a different model")
    assets = manifest.get("assets", {})
    if not assets:
        raise ModelLoadError("No model assets")
    for name, expected_hash in assets.items():
        asset_path = (root / name).resolve()
        if root.resolve() not in asset_path.parents or not asset_path.is_file() or _sha256(asset_path) != expected_hash:
            raise ModelLoadError(f"Missing or modified model asset: {name}")
    approval_path = root / "approval.json"
    approval = _read_json(approval_path) if approval_path.exists() else {}
    approved = (
        approval.get("engineering_approved") is True
        and approval.get("role") == role
        and approval.get("manifest_sha256") == _sha256(manifest_path)
        and approval.get("model_version") == manifest.get("version")
    )
    if require_engineering_approval and not approved and not allow_unapproved_demo:
        raise ModelLoadError("No engineering approval for requested role")
    model_type = manifest.get("model_type")
    if role == "fallback" and model_type != "tfidf":
        raise ModelLoadError("Only TF-IDF can be loaded as first-release fallback")
    if role == "primary" and model_type != "xlmr":
        raise ModelLoadError("First-release primary must be XLM-R")
    if model_type == "tfidf":
        import joblib

        model = joblib.load(root / "model.joblib")
        tokenizer = None
        max_length = None
    elif model_type == "xlmr":
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        # Transformers 4.57.6 emits a Mistral regex warning for XLM-R unless
        # this Mistral-only compatibility switch is explicitly disabled.
        tokenizer = AutoTokenizer.from_pretrained(
            root / "model", local_files_only=True, fix_mistral_regex=False,
        )
        model = AutoModelForSequenceClassification.from_pretrained(root / "model", local_files_only=True)
        if model.config.num_labels != 8:
            raise ModelLoadError("XLM-R classification head must have eight outputs")
        model.to(device).eval()
        max_length = int(manifest["max_length"])
    else:
        raise ModelLoadError(f"Unsupported model type: {model_type}")
    return ModelRuntime(
        model=model, tokenizer=tokenizer, model_type=model_type,
        labels=labels, thresholds=tuple(float(x) for x in thresholds),
        version=manifest["version"], threshold_version=manifest["threshold_version"],
        engineering_approved=approved,
        serving_allowed=bool(approved or allow_unapproved_demo),
        demo_mode=bool(allow_unapproved_demo and not approved),
        max_length=max_length, device=device,
    )
