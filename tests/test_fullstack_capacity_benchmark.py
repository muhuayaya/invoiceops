import json
from pathlib import Path
from subprocess import CompletedProcess

from ml.capacity_fullstack_loadgen import _aggregate_resources, _evaluate, _stats_snapshot, _benchmark_csv


def test_six_service_resource_aggregate_uses_per_snapshot_sums():
    samples = []
    for role in ("proxy", "web", "api", "worker", "postgres", "redis"):
        samples.append({
            "snapshot_id": 1, "sample_offset_seconds": 0.5, "container_role": role,
            "cpu_percent": 100.0, "memory_used_bytes": 100,
        })
    aggregate = _aggregate_resources(samples, [(0.0, 1.0)])

    assert aggregate["complete_snapshot_count"] == 1
    assert aggregate["peak_aggregate_container_memory_bytes"] == 600
    assert aggregate["average_aggregate_container_cpu_core_equivalents"] == 6.0
    assert aggregate["peak_aggregate_container_cpu_core_equivalents"] == 6.0


def test_capacity_csv_contains_only_benchmark_rows_and_required_columns():
    text = _benchmark_csv(3, 2).decode("utf-8")

    assert text.splitlines()[0] == "text,external_id"
    assert len(text.splitlines()) == 4
    assert "capacity-2-0" in text


def test_docker_stats_snapshot_matches_short_container_id(monkeypatch):
    monkeypatch.setattr(
        "ml.capacity_fullstack_loadgen.subprocess.run",
        lambda *_args, **_kwargs: CompletedProcess(
            args=["docker", "stats"], returncode=0,
            stdout=(
                '{"ID":"0123456789ab","Name":"invoiceops-capacity-api-1",'
                '"CPUPerc":"125.00%","MemUsage":"512MiB / 11.54GiB",'
                '"MemPerc":"4.33%"}\n'
            ), stderr="",
        ),
    )

    samples = _stats_snapshot({"api": "0123456789abcdef"}, 3)

    assert len(samples) == 1
    assert samples[0]["container_role"] == "api"
    assert samples[0]["container_name"] == "invoiceops-capacity-api-1"
    assert samples[0]["cpu_percent"] == 125.0
    assert samples[0]["memory_used_bytes"] == 512 * 1024**2


def test_worker_compose_uses_solo_pool_for_supervised_model_processes():
    root = Path(__file__).resolve().parents[1]

    for compose_name in ("compose.yaml", "compose.capacity.full.yaml"):
        compose_text = (root / compose_name).read_text(encoding="utf-8")
        assert "--pool=solo" in compose_text


def test_frozen_capacity_gate_evaluation_fails_closed_on_missing_metrics():
    root = Path(__file__).resolve().parents[1]
    gate = json.loads((root / "ml/gates/engineering-capacity-v1.json").read_text(encoding="utf-8"))
    empty_metric = {"p95_ms": None, "p99_ms": None}
    results = [
        {"concurrency": n, "attempts": 0, "successful_requests": 0,
         "successful_requests_per_second": None, "errors": {},
         "all_attempt_latency_ms": empty_metric}
        for n in (1, 2, 4, 8, 16)
    ]
    soak = {**results[3], "concurrency": 8}
    csv_result = {"model_version_mismatches": 0, "correctness_pass": True}
    resources = {
        "peak_aggregate_container_memory_bytes": 1,
        "average_aggregate_container_cpu_core_equivalents": 1,
        "peak_aggregate_container_cpu_core_equivalents": 1,
    }
    states = {
        role: {"status": "running", "oom_killed": False, "restart_count": 0}
        for role in ("proxy", "web", "api", "worker", "postgres", "redis")
    }

    result = _evaluate(
        gate, {"model_version": "xlmr-c3d15da2f4d2", "role": "primary"},
        results, soak, csv_result, resources, states, states, "complete",
    )

    assert result["status"] == "fail"
    assert not result["checks"]["steady_successful_throughput"]
    assert not result["checks"]["minimum_successful_requests"]
