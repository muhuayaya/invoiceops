"""Send authenticated API load to a loopback-only benchmark app."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import statistics
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import psutil


_TEXTS = (
    "Please confirm payment status for test invoice INV-BENCH-2048.",
    "请核对测试发票 INV-BENCH-2048 的付款状态与收款日期。",
    "Invoice review request for benchmark testing. Please confirm the purchase order, invoice amount, "
    "currency, goods receipt quantity, supplier details, and payment status. "
    "This benchmark text contains no customer or production data. "
    "请确认测试采购订单、发票金额、币种、收货数量、供应商信息及付款状态。",
)


def _parse_size(value: str) -> int | None:
    match = re.fullmatch(r"\s*([\d.]+)\s*([A-Za-z]+)\s*", value)
    if not match:
        return None
    units = {
        "B": 1, "kB": 1000, "KB": 1000, "MB": 1000**2, "GB": 1000**3,
        "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4,
    }
    factor = units.get(match.group(2))
    return int(float(match.group(1)) * factor) if factor else None


def _matches_benchmark_identity(identity: object, benchmark_id: str, model_version: str) -> bool:
    return (
        isinstance(identity, dict)
        and identity.get("benchmark_id") == benchmark_id
        and identity.get("model_version") == model_version
        and identity.get("candidate_engineering_approved") is False
    )


def _container_config(name: str) -> dict:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{json .}}", name],
        check=True, capture_output=True, text=True, timeout=10,
    )
    container = json.loads(result.stdout)
    host = container.get("HostConfig", {})
    return {
        "name": name,
        "container_id": container.get("Id"),
        "image": container.get("Config", {}).get("Image"),
        "image_id": container.get("Image"),
        "nano_cpus": host.get("NanoCpus"),
        "memory_limit_bytes": host.get("Memory"),
    }


def _docker_sample(role: str, name: str) -> dict | None:
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", name],
            check=True, capture_output=True, text=True, timeout=8,
        )
        raw = json.loads(result.stdout.strip())
        used, _, limit = raw.get("MemUsage", "").partition("/")
        cpu_text = raw.get("CPUPerc", "").rstrip("%")
        mem_text = raw.get("MemPerc", "").rstrip("%")
        return {
            "container_role": role,
            "container_name": raw.get("Name", name),
            "sample_monotonic": time.monotonic(),
            "sampled_at_utc": datetime.now(timezone.utc).isoformat(),
            "cpu_percent": float(cpu_text) if cpu_text else None,
            "memory_used_bytes": _parse_size(used),
            "memory_limit_bytes": _parse_size(limit),
            "memory_percent": float(mem_text) if mem_text else None,
        }
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return None


def _sample_container(role: str, name: str, stop: threading.Event, samples: list[dict]) -> None:
    while not stop.is_set():
        sample = _docker_sample(role, name)
        if sample is not None:
            samples.append(sample)
        stop.wait(1.0)


def _percentiles(values: list[float]) -> dict[str, float | None]:
    ordered = sorted(values)

    def nearest_rank(percent: int) -> float | None:
        if not ordered:
            return None
        index = max(0, math.ceil(percent * len(ordered) / 100) - 1)
        return round(ordered[index], 3)

    return {
        "sample_count": len(ordered),
        "p50_ms": nearest_rank(50),
        "p95_ms": nearest_rank(95),
        "p99_ms": nearest_rank(99),
        "method": "nearest_rank",
    }


def _request_outcome(status_code: int, payload: object, expected_model_version: str) -> tuple[str, str | None]:
    if status_code != 200:
        return f"http_{status_code}", None
    if not isinstance(payload, dict):
        return "invalid_response_body", None
    version = payload.get("model_version")
    if version != expected_model_version:
        return "model_version_mismatch", version if isinstance(version, str) else None
    return "success", version


def _summarize_attempts(
    attempts: list[dict], *, measurement_seconds: float,
    measurement_start_offset: float, measurement_end_offset: float,
) -> dict:
    failures = Counter(
        str(attempt["outcome"]) for attempt in attempts if attempt["outcome"] != "success"
    )
    succeeded = [attempt for attempt in attempts if attempt["outcome"] == "success"]
    last_completion = max(
        (attempt["completed_offset_seconds"] for attempt in attempts),
        default=measurement_end_offset,
    )
    completed_window = max(measurement_seconds, last_completion - measurement_start_offset)
    all_latency = _percentiles([float(item["latency_ms"]) for item in attempts])
    successful_latency = _percentiles([float(item["latency_ms"]) for item in succeeded])
    total = len(attempts)
    return {
        "attempts": total,
        "successful_requests": len(succeeded),
        "errors": dict(failures),
        "error_rate": round(sum(failures.values()) / total, 6) if total else None,
        "measurement_seconds": round(measurement_seconds, 3),
        "completion_window_seconds": round(completed_window, 3),
        "drain_seconds": round(max(0.0, last_completion - measurement_end_offset), 3),
        "completion_end_offset_seconds": round(max(last_completion, measurement_end_offset), 3),
        "attempts_per_second": round(total / completed_window, 3) if completed_window else None,
        "successful_requests_per_second": round(len(succeeded) / completed_window, 3) if completed_window else None,
        "all_attempt_latency_ms": all_latency,
        "successful_latency_ms": successful_latency,
        "response_model_versions": dict(Counter(
            attempt["model_version"] for attempt in succeeded if attempt.get("model_version")
        )),
    }


def _resource_sampling_summary(samples: list[dict], roles: tuple[str, ...]) -> dict:
    by_role = {
        role: sorted(
            (sample for sample in samples if sample["container_role"] == role),
            key=lambda sample: sample["sample_monotonic"],
        )
        for role in roles
    }
    containers = {}
    for role, role_samples in by_role.items():
        intervals = [
            later["sample_monotonic"] - earlier["sample_monotonic"]
            for earlier, later in zip(role_samples, role_samples[1:])
        ]
        containers[role] = {
            "sample_count": len(role_samples),
            "actual_interval_seconds": {
                "min": round(min(intervals), 3),
                "median": round(statistics.median(intervals), 3),
                "max": round(max(intervals), 3),
            } if intervals else None,
            "peak_cpu_percent": max(
                (sample["cpu_percent"] for sample in role_samples if sample["cpu_percent"] is not None),
                default=None,
            ),
            "peak_memory_used_bytes": max(
                (sample["memory_used_bytes"] for sample in role_samples if sample["memory_used_bytes"] is not None),
                default=None,
            ),
            "peak_memory_percent": max(
                (sample["memory_percent"] for sample in role_samples if sample["memory_percent"] is not None),
                default=None,
            ),
        }
    return {
        "requested_interval_seconds": 1.0,
        "status": "complete" if all(containers[role]["sample_count"] > 1 for role in roles) else "incomplete",
        "containers": containers,
    }


def _phase_resource_peak(samples: list[dict], result: dict, roles: tuple[str, ...]) -> dict:
    start = result["warmup_start_offset_seconds"]
    end = result["completion_end_offset_seconds"]
    relevant = [
        sample for sample in samples
        if start <= sample["sample_offset_seconds"] <= end
    ]
    return {
        role: {
            "cpu_percent": max(
                (sample["cpu_percent"] for sample in relevant
                 if sample["container_role"] == role and sample["cpu_percent"] is not None),
                default=None,
            ),
            "memory_used_bytes": max(
                (sample["memory_used_bytes"] for sample in relevant
                 if sample["container_role"] == role and sample["memory_used_bytes"] is not None),
                default=None,
            ),
            "memory_percent": max(
                (sample["memory_percent"] for sample in relevant
                 if sample["container_role"] == role and sample["memory_percent"] is not None),
                default=None,
            ),
        }
        for role in roles
    }


def _command_output(*args: str) -> str | None:
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() or result.stderr.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _source_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "ml/capacity_loadgen.py",
        root / "ml/benchmark_api.py",
        root / "compose.capacity.yaml",
        root / "run_compose_capacity_benchmark.ps1",
        root / "Dockerfile",
        root / "apps/api/main.py",
        root / "src/invoiceops/application/classifier.py",
        root / "src/invoiceops/ml_runtime/supervisor.py",
        root / "src/invoiceops/ml_runtime/runtime.py",
        root / "src/invoiceops/adapters/db.py",
    )
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


def _environment_snapshot() -> dict:
    docker_memory = _command_output("docker", "info", "--format", "{{.MemTotal}}")
    docker_cpus = _command_output("docker", "info", "--format", "{{.NCPU}}")

    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "logical_cpu_count": os.cpu_count(),
        "host_memory_total_bytes": psutil.virtual_memory().total,
        "docker_server_memory_bytes": int(docker_memory) if docker_memory and docker_memory.isdigit() else None,
        "docker_server_cpu_count": int(docker_cpus) if docker_cpus and docker_cpus.isdigit() else None,
        "docker_engine_version": _command_output("docker", "version", "--format", "{{.Server.Version}}"),
        "docker_compose_version": _command_output("docker", "compose", "version", "--short"),
    }


def _run_level(
    base_url: str, token: str, concurrency: int, warmup: float, duration: float,
    suite_start: float, expected_model_version: str,
) -> dict:
    barrier = threading.Barrier(concurrency + 1)
    start_event = threading.Event()
    phase: dict[str, float] = {}

    def worker(worker_id: int):
        attempts: list[dict] = []
        request_index = 0
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0, connect=5.0),
        ) as client:
            barrier.wait()
            start_event.wait()
            while time.monotonic() < phase["end"]:
                started = time.monotonic()
                measured = phase["measure_start"] <= started < phase["end"]
                payload = {"text": _TEXTS[(worker_id + request_index) % len(_TEXTS)], "source": "api"}
                request_index += 1
                try:
                    response = client.post("/v1/classifications", json=payload)
                    if measured:
                        completed = time.monotonic()
                        try:
                            response_body = response.json()
                        except ValueError:
                            response_body = None
                        outcome, model_version = _request_outcome(
                            response.status_code, response_body, expected_model_version,
                        )
                        attempts.append({
                            "outcome": outcome,
                            "latency_ms": (completed - started) * 1000,
                            "started_offset_seconds": started - suite_start,
                            "completed_offset_seconds": completed - suite_start,
                            "model_version": model_version,
                        })
                except httpx.TimeoutException as exc:
                    if measured:
                        completed = time.monotonic()
                        attempts.append({
                            "outcome": "timeout:" + type(exc).__name__,
                            "latency_ms": (completed - started) * 1000,
                            "started_offset_seconds": started - suite_start,
                            "completed_offset_seconds": completed - suite_start,
                            "model_version": None,
                        })
                except httpx.HTTPError as exc:
                    if measured:
                        completed = time.monotonic()
                        attempts.append({
                            "outcome": "http_error:" + type(exc).__name__,
                            "latency_ms": (completed - started) * 1000,
                            "started_offset_seconds": started - suite_start,
                            "completed_offset_seconds": completed - suite_start,
                            "model_version": None,
                        })
        return attempts

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker, worker_id) for worker_id in range(concurrency)]
        barrier.wait()
        phase_start = time.monotonic()
        phase["measure_start"] = phase_start + warmup
        phase["end"] = phase["measure_start"] + duration
        start_event.set()
        time.sleep(warmup + duration)
        worker_results = [future.result() for future in futures]

    attempts = [attempt for result in worker_results for attempt in result]
    measurement_start_offset = phase["measure_start"] - suite_start
    measurement_end_offset = phase["end"] - suite_start
    return {
        "concurrency": concurrency,
        "warmup_seconds": warmup,
        "warmup_start_offset_seconds": round(phase_start - suite_start, 3),
        "measurement_start_offset_seconds": round(measurement_start_offset, 3),
        "measurement_end_offset_seconds": round(measurement_end_offset, 3),
        **_summarize_attempts(
            attempts,
            measurement_seconds=duration,
            measurement_start_offset=measurement_start_offset,
            measurement_end_offset=measurement_end_offset,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--concurrency", default="1,2,4,8,16")
    parser.add_argument("--warmup-seconds", type=float, default=2.0)
    parser.add_argument("--duration-seconds", type=float, default=30.0)
    parser.add_argument("--api-container", required=True, help="isolated benchmark API container")
    parser.add_argument("--postgres-container", required=True, help="isolated benchmark PostgreSQL container")
    parser.add_argument("--redis-container", required=True, help="isolated benchmark Redis container")
    parser.add_argument("--benchmark-id", required=True, help="nonce reported by the isolated benchmark API")
    parser.add_argument("--manifest", default="ml/artifacts/xlmr-v2/manifest.json")
    parser.add_argument("--output")
    args = parser.parse_args()

    parsed = urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("benchmark traffic must use an HTTP loopback URL; do not target production or LAN services")
    if args.warmup_seconds < 0 or args.duration_seconds <= 0:
        parser.error("warmup must be nonnegative and duration must be positive")
    try:
        concurrency = [int(value) for value in args.concurrency.split(",")]
    except ValueError:
        parser.error("concurrency must be comma-separated positive integers")
    if not concurrency or any(value < 1 or value > 64 for value in concurrency) or len(set(concurrency)) != len(concurrency):
        parser.error("concurrency values must be unique integers from 1 to 64")

    model_manifest_path = Path(args.manifest)
    manifest_bytes = model_manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if args.output:
        output = Path(args.output)
    else:
        report_version = re.sub(r"[^A-Za-z0-9._-]", "_", str(manifest.get("version", "unknown")))
        output = Path("ml/benchmarks") / f"{report_version}-api-compose-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    if output.exists():
        parser.error(f"refusing to overwrite existing benchmark report: {output}")

    with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")
        if health.status_code != 200 or ready.status_code != 200:
            parser.error("isolated benchmark API must be healthy and ready before generating load")
        model_version = ready.json().get("model_version")
        if model_version != manifest.get("version"):
            parser.error("benchmark API model version differs from the supplied manifest")
        identity = client.get(
            "/_benchmark/identity",
            headers={"X-InvoiceOps-Benchmark-ID": args.benchmark_id},
        )
        if identity.status_code != 200:
            parser.error("target API does not expose the isolated benchmark identity")
        if not _matches_benchmark_identity(identity.json(), args.benchmark_id, model_version):
            parser.error("target API benchmark identity does not match this isolated run")
        email = f"capacity-{uuid4().hex}@example.test"
        password = "CapacityBenchmark2026"
        registered = client.post("/v1/auth/register", json={"email": email, "password": password})
        if registered.status_code != 200:
            parser.error(f"unable to create disposable benchmark account (status {registered.status_code})")
        login = client.post("/v1/auth/login", json={"email": email, "password": password})
        if login.status_code != 200:
            parser.error(f"unable to authenticate disposable benchmark account (status {login.status_code})")
        token = login.json()["token"]

    container_names = {
        "api": args.api_container,
        "postgres": args.postgres_container,
        "redis": args.redis_container,
    }
    resource_limits = {
        role: _container_config(name)
        for role, name in container_names.items()
    }
    monitor_stop = threading.Event()
    resource_samples: list[dict] = []
    suite_start = time.monotonic()
    monitors = [
        threading.Thread(
            target=_sample_container,
            args=(role, name, monitor_stop, resource_samples),
            daemon=True,
        )
        for role, name in container_names.items()
    ]
    for monitor in monitors:
        monitor.start()
    results = []
    for workers in concurrency:
        result = _run_level(
            args.base_url, token, workers, args.warmup_seconds, args.duration_seconds,
            suite_start, model_version,
        )
        results.append(result)
    monitor_stop.set()
    for monitor in monitors:
        monitor.join(timeout=10.0)
    suite_elapsed = time.monotonic() - suite_start
    for sample in resource_samples:
        sample["sample_offset_seconds"] = sample["sample_monotonic"] - suite_start

    for result in results:
        result["container_resource_peak"] = _phase_resource_peak(resource_samples, result, tuple(container_names))

    resource_sampling = _resource_sampling_summary(resource_samples, tuple(container_names))
    resource_sampling["monitor_threads_stopped"] = all(not monitor.is_alive() for monitor in monitors)
    if not resource_sampling["monitor_threads_stopped"]:
        resource_sampling["status"] = "incomplete"
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 2,
        "benchmark_type": "compose_api_capacity_characterization_not_acceptance",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "service": "isolated Compose API service; direct loopback HTTP; Caddy/TLS excluded",
        "database": "disposable Compose PostgreSQL 17 and Redis 7.4; not the active Compose services",
        "benchmark_id": args.benchmark_id,
        "environment": _environment_snapshot(),
        "source_sha256": _source_hashes(),
        "target_profile": {
            "compose_file": "compose.capacity.yaml",
            "container_limits": "not configured; Docker Desktop VM global limits still apply",
            "model_device": "cpu",
            "inference_path": "ModelSupervisor child process and IPC",
            "migrations": "Alembic upgrade head",
            "production_stack_policy": "benchmark runner refuses to start while invoiceops Compose containers are running",
        },
        "candidate": {
            "role": "primary",
            "model_version": manifest.get("version"),
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "engineering_approved": False,
        },
        "input_policy": "benchmark-only English, Chinese, and mixed texts; no project CSVs read",
        "load_profile": {
            "concurrency_levels": concurrency,
            "warmup_seconds_per_level": args.warmup_seconds,
            "measurement_seconds_per_level": args.duration_seconds,
            "closed_loop_workers": True,
            "latency_percentile_method": "nearest_rank",
        },
        "resource_limits": resource_limits,
        "resource_sampling": resource_sampling,
        "total_elapsed_seconds": round(suite_elapsed, 3),
        "results": results,
        "gate_status": "not_evaluated_no_capacity_sla_frozen",
        "limitations": [
            "This measures API, disposable PostgreSQL, and Redis; it excludes Caddy/TLS, LAN networking, Streamlit, and the worker.",
            "The benchmark-only wrapper lets the isolated API serve the unapproved candidate; the child runtime remains unapproved and no approval record is written.",
            "The runner refuses to start with the invoiceops Compose stack running; unrelated host workloads and Docker Desktop VM limits may still affect measurements.",
            "No pass/fail capacity conclusion is possible until the owner freezes a target load and latency/error SLO.",
        ],
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "model_version": manifest.get("version"), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
