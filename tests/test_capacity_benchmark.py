import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from invoiceops.adapters.db import Base
from ml.benchmark_api import _BenchmarkCandidate
from ml.benchmark_api import _create_app
from ml.capacity_loadgen import (
    _matches_benchmark_identity,
    _parse_size,
    _request_outcome,
    _resource_sampling_summary,
    _environment_snapshot,
    _source_hashes,
    _summarize_attempts,
)


def test_benchmark_adapter_does_not_change_candidate_approval_state(monkeypatch, tmp_path):
    class Supervisor:
        def __init__(self, *args, **kwargs):
            self.engineering_approved = False
            self.ready = False
            self.labels = ("label",)
            self.thresholds = (0.5,)
            self.version = "candidate-v1"
            self.threshold_version = "candidate-v1-dev"

        def start(self):
            self.ready = True
            return self

        def predict_proba(self, texts):
            return [[0.7] for _ in texts]

        def close(self):
            self.ready = False

    monkeypatch.setattr("ml.benchmark_api._BenchmarkSupervisor", Supervisor)
    adapter = _BenchmarkCandidate(tmp_path, tmp_path / "taxonomy.json", "run-123")
    adapter.start()

    assert adapter.engineering_approved is True
    assert adapter.candidate_engineering_approved is False
    assert adapter.ready
    assert adapter.version == "candidate-v1"
    assert adapter.predict_proba(["benchmark text"]) == [[0.7]]
    adapter.close()
    assert not adapter.ready


def test_capacity_loadgen_requires_matching_benchmark_identity():
    identity = {
        "benchmark_id": "run-123",
        "model_version": "candidate-v1",
        "candidate_engineering_approved": False,
    }

    assert _matches_benchmark_identity(identity, "run-123", "candidate-v1")
    assert not _matches_benchmark_identity(identity, "other-run", "candidate-v1")
    assert not _matches_benchmark_identity(identity, "run-123", "other-model")
    identity["candidate_engineering_approved"] = True
    assert not _matches_benchmark_identity(identity, "run-123", "candidate-v1")


def test_capacity_report_parses_docker_memory_units():
    assert _parse_size("1.5GiB") == int(1.5 * 1024**3)
    assert _parse_size("64MB") == 64 * 1000**2
    assert _parse_size("unavailable") is None


def test_capacity_report_captures_source_and_tolerates_missing_docker(monkeypatch):
    monkeypatch.setattr("ml.capacity_loadgen._command_output", lambda *_args: None)

    sources = _source_hashes()
    environment = _environment_snapshot()

    assert {
        "ml/capacity_loadgen.py",
        "ml/benchmark_api.py",
        "compose.capacity.yaml",
        "apps/api/main.py",
        "src/invoiceops/ml_runtime/supervisor.py",
        "src/invoiceops/ml_runtime/runtime.py",
    }.issubset(sources)
    assert all(len(digest) == 64 for digest in sources.values())
    assert environment["docker_server_memory_bytes"] is None
    assert environment["docker_server_cpu_count"] is None
    assert environment["docker_engine_version"] is None


def test_capacity_metrics_include_failed_latency_and_request_drain():
    attempts = [
        {
            "outcome": "success", "latency_ms": 100.0,
            "completed_offset_seconds": 1.5, "model_version": "model-v1",
        },
        {
            "outcome": "timeout:ReadTimeout", "latency_ms": 1000.0,
            "completed_offset_seconds": 4.4, "model_version": None,
        },
        {
            "outcome": "http_503", "latency_ms": 250.0,
            "completed_offset_seconds": 5.0, "model_version": None,
        },
    ]

    result = _summarize_attempts(
        attempts,
        measurement_seconds=3.0,
        measurement_start_offset=1.0,
        measurement_end_offset=4.0,
    )

    assert result["attempts"] == 3
    assert result["successful_requests"] == 1
    assert result["errors"] == {"timeout:ReadTimeout": 1, "http_503": 1}
    assert result["error_rate"] == 0.666667
    assert result["drain_seconds"] == 1.0
    assert result["completion_window_seconds"] == 4.0
    assert result["attempts_per_second"] == 0.75
    assert result["all_attempt_latency_ms"]["p99_ms"] == 1000.0
    assert result["all_attempt_latency_ms"]["sample_count"] == 3
    assert result["successful_latency_ms"]["p99_ms"] == 100.0
    assert result["response_model_versions"] == {"model-v1": 1}


def test_capacity_requests_must_return_the_pinned_model_version():
    assert _request_outcome(200, {"model_version": "model-v1"}, "model-v1") == (
        "success", "model-v1",
    )
    assert _request_outcome(200, {"model_version": "other"}, "model-v1") == (
        "model_version_mismatch", "other",
    )
    assert _request_outcome(200, {}, "model-v1") == ("model_version_mismatch", None)
    assert _request_outcome(503, {"model_version": "model-v1"}, "model-v1") == (
        "http_503", None,
    )


def test_resource_sampling_reports_actual_intervals_and_incomplete_services():
    samples = [
        {"container_role": "api", "sample_monotonic": 10.0, "cpu_percent": 15.0,
         "memory_used_bytes": 100, "memory_percent": 10.0},
        {"container_role": "api", "sample_monotonic": 11.4, "cpu_percent": 70.0,
         "memory_used_bytes": 120, "memory_percent": 12.0},
        {"container_role": "postgres", "sample_monotonic": 10.2, "cpu_percent": 5.0,
         "memory_used_bytes": 50, "memory_percent": 5.0},
        {"container_role": "postgres", "sample_monotonic": 11.5, "cpu_percent": 7.0,
         "memory_used_bytes": 60, "memory_percent": 6.0},
        {"container_role": "redis", "sample_monotonic": 10.3, "cpu_percent": 1.0,
         "memory_used_bytes": 10, "memory_percent": 1.0},
    ]

    result = _resource_sampling_summary(samples, ("api", "postgres", "redis"))

    assert result["status"] == "incomplete"
    assert result["containers"]["api"]["sample_count"] == 2
    assert result["containers"]["api"]["actual_interval_seconds"] == {
        "min": 1.4, "median": 1.4, "max": 1.4,
    }
    assert result["containers"]["api"]["peak_cpu_percent"] == 70.0
    assert result["containers"]["redis"]["actual_interval_seconds"] is None


def test_benchmark_api_exposes_only_nonce_bound_identity_and_pinned_version(monkeypatch, tmp_path):
    model_path = tmp_path / "candidate"
    model_path.mkdir()
    model_version = "benchmark-candidate-v1"
    (model_path / "manifest.json").write_text(
        json.dumps({"model_type": "xlmr", "version": model_version}), encoding="utf-8",
    )
    taxonomy_path = Path("configs/taxonomy.json").resolve()
    database_url = f"sqlite:///{tmp_path / 'capacity-api.sqlite'}"
    setup_engine = create_engine(database_url)
    Base.metadata.create_all(setup_engine)
    setup_engine.dispose()

    class Supervisor:
        def __init__(self, path, *, taxonomy_path, **_kwargs):
            self.engineering_approved = False
            self.ready = False
            self.version = json.loads((path / "manifest.json").read_text())["version"]
            self.threshold_version = self.version + "-thresholds"
            self.labels = tuple(json.loads(taxonomy_path.read_text())["labels"])
            self.thresholds = (0.5,) * len(self.labels)

        def start(self):
            self.ready = True
            return self

        def predict_proba(self, texts):
            return [[0.9] * len(self.labels) for _ in texts]

        def close(self):
            self.ready = False

    monkeypatch.setattr("ml.benchmark_api._BenchmarkSupervisor", Supervisor)
    monkeypatch.setenv("INVOICEOPS_DATABASE_URL", database_url)
    monkeypatch.setenv("INVOICEOPS_TAXONOMY", str(taxonomy_path))
    app = _create_app(model_path, database_url, taxonomy_path, "run-123")

    try:
        with TestClient(app) as client:
            assert client.get("/readyz").json()["model_version"] == model_version
            assert client.get(
                "/_benchmark/identity",
                headers={"X-InvoiceOps-Benchmark-ID": "wrong-run"},
            ).status_code == 404
            identity = client.get(
                "/_benchmark/identity",
                headers={"X-InvoiceOps-Benchmark-ID": "run-123"},
            )
            assert identity.json() == {
                "benchmark_id": "run-123",
                "model_version": model_version,
                "candidate_role": "primary",
                "candidate_engineering_approved": False,
            }

            registration = client.post(
                "/v1/auth/register",
                json={"email": "capacity-test@example.test", "password": "CapacityTest123"},
            )
            assert registration.status_code == 200
            login = client.post(
                "/v1/auth/login",
                json={"email": "capacity-test@example.test", "password": "CapacityTest123"},
            )
            result = client.post(
                "/v1/classifications",
                headers={"Authorization": "Bearer " + login.json()["token"]},
                json={"text": "benchmark text", "source": "api"},
            )
            assert result.status_code == 200
            assert result.json()["model_version"] == model_version
            assert result.json()["degraded"] is False
    finally:
        app.state.engine.dispose()


def test_benchmark_api_serves_unapproved_tfidf_only_in_fallback_slot(monkeypatch, tmp_path):
    model_path = tmp_path / "tfidf-candidate"
    model_path.mkdir()
    model_version = "benchmark-tfidf-v1"
    (model_path / "manifest.json").write_text(
        json.dumps({"model_type": "tfidf", "version": model_version}), encoding="utf-8",
    )
    taxonomy_path = Path("configs/taxonomy.json").resolve()
    database_url = f"sqlite:///{tmp_path / 'capacity-tfidf.sqlite'}"
    setup_engine = create_engine(database_url)
    Base.metadata.create_all(setup_engine)
    setup_engine.dispose()

    class Supervisor:
        def __init__(self, path, *, role, taxonomy_path, **_kwargs):
            assert role == "fallback"
            self.engineering_approved = False
            self.ready = False
            self.version = json.loads((path / "manifest.json").read_text())["version"]
            self.threshold_version = self.version + "-thresholds"
            self.labels = tuple(json.loads(taxonomy_path.read_text())["labels"])
            self.thresholds = (0.5,) * len(self.labels)

        def start(self):
            self.ready = True
            return self

        def predict_proba(self, texts):
            return [[0.9] * len(self.labels) for _ in texts]

        def close(self):
            self.ready = False

    monkeypatch.setattr("ml.benchmark_api._BenchmarkSupervisor", Supervisor)
    monkeypatch.setenv("INVOICEOPS_DATABASE_URL", database_url)
    monkeypatch.setenv("INVOICEOPS_TAXONOMY", str(taxonomy_path))
    app = _create_app(model_path, database_url, taxonomy_path, "run-456", "fallback")

    try:
        with TestClient(app) as client:
            assert client.get("/readyz").json() == {
                "status": "degraded", "model_version": model_version, "degraded": True,
            }
            identity = client.get(
                "/_benchmark/identity",
                headers={"X-InvoiceOps-Benchmark-ID": "run-456"},
            )
            assert identity.json() == {
                "benchmark_id": "run-456",
                "model_version": model_version,
                "candidate_role": "fallback",
                "candidate_engineering_approved": False,
            }
    finally:
        app.state.engine.dispose()
