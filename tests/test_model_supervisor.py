"""Spawned model process lifecycle and timeout behavior."""

from __future__ import annotations

import hashlib
import json

import time
from pathlib import Path

import joblib
import numpy as np
import psutil
import pytest

from invoiceops.application.classifier import classify
from invoiceops.ml_runtime.supervisor import (
    ModelInferenceTimeout,
    ModelNotReady,
    ModelSupervisor,
    ModelSupervisorStartupError,
)


LABELS = [
    "DUPLICATE_INVOICE", "MISSING_PO_OR_RECEIPT", "OTHER_REVIEW",
    "PAYMENT_STATUS", "PRICE_VARIANCE", "QUANTITY_RECEIPT_VARIANCE",
    "SUPPLIER_MASTER_CHANGE", "TAX_CURRENCY_AMOUNT",
]


class PickleEightOutputModel:
    """Small importable joblib fixture that can reproduce one hung inference."""

    def __init__(self, hang_marker: str | None = None):
        self.hang_marker = hang_marker

    def __getstate__(self):
        return {"hang_marker": self.hang_marker}

    def __setstate__(self, state):
        self.hang_marker = state["hang_marker"]
        # Make the first replacement's loading window deterministic in the
        # recovery test. The marker is only created after the first load.
        if self.hang_marker and Path(self.hang_marker).exists():
            time.sleep(0.6)

    def predict_proba(self, texts):
        if self.hang_marker and "hang" in texts:
            Path(self.hang_marker).write_text("restart loading is intentionally delayed")
            time.sleep(30)
        result = np.full((len(texts), 8), 0.1)
        result[:, 0] = 0.9
        return result


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(root: Path, taxonomy_path: Path, *, hang_marker: Path | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    model_file = root / "model.joblib"
    joblib.dump(PickleEightOutputModel(str(hang_marker) if hang_marker else None), model_file)
    manifest = {
        "model_type": "tfidf",
        "version": "supervisor-test-v1",
        "threshold_version": "supervisor-test-threshold-v1",
        "threshold_model_version": "supervisor-test-v1",
        "taxonomy_version": "invoiceops-v1",
        "labels": LABELS,
        "thresholds": [0.5] * 8,
        "assets": {"model.joblib": _sha(model_file)},
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (root / "approval.json").write_text(json.dumps({
        "engineering_approved": True,
        "role": "fallback",
        "model_version": manifest["version"],
        "manifest_sha256": _sha(manifest_path),
    }), encoding="utf-8")


@pytest.fixture
def model_bundle(tmp_path):
    taxonomy = tmp_path / "taxonomy.json"
    taxonomy.write_text(json.dumps({
        "taxonomy_version": "invoiceops-v1",
        "labels": LABELS,
    }), encoding="utf-8")
    marker = tmp_path / "hang-started.txt"
    primary = tmp_path / "primary"
    fallback = tmp_path / "fallback"
    _artifact(primary, taxonomy, hang_marker=marker)
    _artifact(fallback, taxonomy)
    return primary, fallback, taxonomy, marker


def _supervisor(path: Path, taxonomy: Path, *, timeout: float = 1.0) -> ModelSupervisor:
    return ModelSupervisor(
        path,
        role="fallback",
        taxonomy_path=taxonomy,
        inference_timeout_seconds=timeout,
        startup_timeout_seconds=3.0,
    )


def test_spawned_tfidf_prediction_and_close(model_bundle):
    primary, _, taxonomy, _ = model_bundle
    supervisor = _supervisor(primary, taxonomy)
    try:
        assert supervisor.start() is supervisor
        assert supervisor.ready
        assert supervisor.labels == tuple(LABELS)
        assert supervisor.engineering_approved
        assert supervisor.version == "supervisor-test-v1"
        probabilities = supervisor.predict_proba(["invoice text"])
        assert len(probabilities) == 1
        assert len(probabilities[0]) == 8
        assert probabilities[0][0] == pytest.approx(0.9)
    finally:
        pid = supervisor.pid
        supervisor.close()
    assert not supervisor.ready
    if pid is not None:
        assert not psutil.pid_exists(pid)


def test_unapproved_model_requires_explicit_isolated_benchmark_override(model_bundle):
    primary, _, taxonomy, _ = model_bundle
    (primary / "approval.json").unlink()

    production = _supervisor(primary, taxonomy)
    try:
        with pytest.raises(ModelSupervisorStartupError, match="No engineering approval"):
            production.start()
    finally:
        production.close()

    class BenchmarkOnlySupervisor(ModelSupervisor):
        _require_engineering_approval = False

    benchmark = BenchmarkOnlySupervisor(
        primary,
        role="fallback",
        taxonomy_path=taxonomy,
        inference_timeout_seconds=1.0,
        startup_timeout_seconds=3.0,
    )
    try:
        benchmark.start()
        assert benchmark.ready
        assert benchmark.engineering_approved is False
        assert benchmark.predict_proba(["benchmark text"])[0][0] == pytest.approx(0.9)
        assert not (primary / "approval.json").exists()
    finally:
        benchmark.close()

    relaxed = ModelSupervisor(
        primary,
        role="fallback",
        taxonomy_path=taxonomy,
        inference_timeout_seconds=1.0,
        startup_timeout_seconds=3.0,
        allow_unapproved_demo=True,
    )
    try:
        relaxed.start()
        assert relaxed.engineering_approved is False
        assert relaxed.serving_allowed is True
        assert relaxed.demo_mode is True
    finally:
        relaxed.close()


def test_timeout_kills_process_fails_fast_while_restarting_then_recovers(model_bundle):
    primary, _, taxonomy, _ = model_bundle
    supervisor = _supervisor(primary, taxonomy, timeout=0.2)
    try:
        supervisor.start()
        old_pid = supervisor.pid
        with pytest.raises(ModelInferenceTimeout, match="terminated and joined"):
            supervisor.predict_proba(["hang"])
        assert not psutil.pid_exists(old_pid)

        started = time.monotonic()
        with pytest.raises(ModelNotReady, match="still starting"):
            supervisor.predict_proba(["quick retry"])
        assert time.monotonic() - started < 0.3

        deadline = time.monotonic() + 4.0
        while not supervisor.ready and time.monotonic() < deadline:
            time.sleep(0.02)
        assert supervisor.ready
        assert supervisor.predict_proba(["recovered"])[0][0] == pytest.approx(0.9)
    finally:
        supervisor.close()


def test_timed_out_primary_uses_approved_supervised_fallback(model_bundle):
    primary_path, fallback_path, taxonomy, _ = model_bundle
    primary = _supervisor(primary_path, taxonomy, timeout=0.2).start()
    fallback = _supervisor(fallback_path, taxonomy).start()
    try:
        result = classify(
            "hang",
            primary=primary,
            fallback=fallback,
            label_order=LABELS,
            taxonomy_version="invoiceops-v1",
        )
        assert result.degraded
        assert result.fallback_reason == "ModelInferenceTimeout"
        assert result.model_version == "supervisor-test-v1"
        assert result.labels == ("DUPLICATE_INVOICE",)
    finally:
        primary.close()
        fallback.close()
