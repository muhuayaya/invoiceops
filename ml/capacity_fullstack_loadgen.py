"""Run the frozen engineering capacity profile against the isolated six-service stack."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from ml.capacity_loadgen import (
    _TEXTS,
    _container_config,
    _environment_snapshot,
    _matches_benchmark_identity,
    _parse_size,
    _percentiles,
    _request_outcome,
    _resource_sampling_summary,
    _summarize_attempts,
)


_CONTAINER_ROLES = ("proxy", "web", "api", "worker", "postgres", "redis")


def _source_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "ml/capacity_fullstack_loadgen.py",
        root / "ml/benchmark_api.py",
        root / "ml/benchmark_worker.py",
        root / "ml/benchmark_worker.py",
        root / "compose.capacity.full.yaml",
        root / "Dockerfile.capacity",
        root / "infra/caddy/Caddyfile.capacity",
        root / "Dockerfile",
        root / "apps/api/main.py",
        root / "apps/worker/tasks.py",
        root / "src/invoiceops/application/classifier.py",
        root / "src/invoiceops/application/batch.py",
        root / "src/invoiceops/ml_runtime/supervisor.py",
        root / "src/invoiceops/ml_runtime/runtime.py",
        root / "src/invoiceops/adapters/db.py",
    )
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths if path.is_file()
    }


def _container_states(container_names: dict[str, str], expected_project: str) -> dict[str, dict]:
    states = {}
    for role, name in container_names.items():
        result = subprocess.run(
            ["docker", "inspect", name], check=True, capture_output=True,
            text=True, timeout=10,
        )
        container = json.loads(result.stdout)[0]
        labels = container.get("Config", {}).get("Labels", {})
        if labels.get("com.docker.compose.project") != expected_project:
            raise ValueError(f"{role} container is not owned by the isolated benchmark project")
        if labels.get("com.docker.compose.service") != role:
            raise ValueError(f"container is not the expected isolated {role} service")
        states[role] = {
            "container_id": container.get("Id"),
            "status": container.get("State", {}).get("Status"),
            "oom_killed": bool(container.get("State", {}).get("OOMKilled")),
            "restart_count": int(container.get("RestartCount", 0)),
        }
    return states


def _stats_snapshot(container_names: dict[str, str], snapshot_id: int) -> list[dict]:
    sampled_at = time.monotonic()
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *container_names.values()],
        check=True, capture_output=True, text=True, timeout=20,
    )
    role_by_id = {container_id.lower(): role for role, container_id in container_names.items()}
    role_by_name = {name: role for role, name in container_names.items()}
    samples = []
    for line in result.stdout.splitlines():
        raw = json.loads(line)
        container_id = raw.get("ID", "").lower()
        role = next((
            candidate_role for expected_id, candidate_role in role_by_id.items()
            if expected_id.startswith(container_id) or container_id.startswith(expected_id)
        ), None) if container_id else None
        if role is None:
            role = role_by_name.get(raw.get("Name"))
        if role is None:
            continue
        cpu_text = raw.get("CPUPerc", "").rstrip("%")
        used, _, limit = raw.get("MemUsage", "").partition("/")
        samples.append({
            "snapshot_id": snapshot_id,
            "container_role": role,
            "container_name": raw.get("Name") or container_id,
            "sample_monotonic": sampled_at,
            "cpu_percent": float(cpu_text) if cpu_text else None,
            "memory_used_bytes": _parse_size(used),
            "memory_limit_bytes": _parse_size(limit),
            "memory_percent": float(raw.get("MemPerc", "").rstrip("%")) if raw.get("MemPerc") else None,
        })
    return samples


def _monitor_containers(container_names: dict[str, str], stop: threading.Event, samples: list[dict], errors: list[str]) -> None:
    snapshot_id = 0
    while not stop.is_set():
        started = time.monotonic()
        try:
            samples.extend(_stats_snapshot(container_names, snapshot_id))
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
            errors.append(type(exc).__name__)
        snapshot_id += 1
        stop.wait(max(0.0, 1.0 - (time.monotonic() - started)))


def _aggregate_resources(samples: list[dict], measurement_windows: list[tuple[float, float]]) -> dict:
    measured = [
        sample for sample in samples
        if any(start <= sample["sample_offset_seconds"] <= end for start, end in measurement_windows)
    ]
    snapshots: dict[int, list[dict]] = {}
    for sample in measured:
        snapshots.setdefault(sample["snapshot_id"], []).append(sample)
    rows = []
    for items in snapshots.values():
        roles = {sample["container_role"] for sample in items}
        if roles != set(_CONTAINER_ROLES):
            continue
        cpu = sum(sample["cpu_percent"] or 0.0 for sample in items) / 100.0
        memory = sum(sample["memory_used_bytes"] or 0 for sample in items)
        rows.append({"cpu_core_equivalents": cpu, "memory_bytes": memory})
    return {
        "complete_snapshot_count": len(rows),
        "peak_aggregate_container_memory_bytes": max((row["memory_bytes"] for row in rows), default=None),
        "average_aggregate_container_cpu_core_equivalents": round(
            sum(row["cpu_core_equivalents"] for row in rows) / len(rows), 3,
        ) if rows else None,
        "peak_aggregate_container_cpu_core_equivalents": round(
            max((row["cpu_core_equivalents"] for row in rows), default=0.0), 3,
        ) if rows else None,
        "cpu_sampling_method": "sum of six Docker stats CPU percentages per snapshot divided by 100",
        "memory_sampling_method": "sum of six Docker stats memory-used values per snapshot",
        "measurement_windows_seconds": measurement_windows,
    }


def _run_level(
    base_url: str, token: str, concurrency: int, warmup: float, duration: float,
    suite_start: float, expected_model_version: str, verify: str,
) -> dict:
    barrier = threading.Barrier(concurrency + 1)
    start_event = threading.Event()
    phase: dict[str, float] = {}

    def worker(worker_id: int):
        attempts = []
        request_index = 0
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0, connect=5.0),
            verify=verify,
        trust_env=False,
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
                            "outcome": outcome, "latency_ms": (completed - started) * 1000,
                            "started_offset_seconds": started - suite_start,
                            "completed_offset_seconds": completed - suite_start,
                            "model_version": model_version,
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
        attempts = [attempt for future in futures for attempt in future.result()]

    measurement_start = phase["measure_start"] - suite_start
    measurement_end = phase["end"] - suite_start
    return {
        "concurrency": concurrency,
        "warmup_seconds": warmup,
        "warmup_start_offset_seconds": round(phase_start - suite_start, 3),
        "measurement_start_offset_seconds": round(measurement_start, 3),
        "measurement_end_offset_seconds": round(measurement_end, 3),
        **_summarize_attempts(
            attempts, measurement_seconds=duration,
            measurement_start_offset=measurement_start,
            measurement_end_offset=measurement_end,
        ),
    }


def _benchmark_csv(row_count: int, worker_id: int) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("text", "external_id"))
    for index in range(row_count):
        text = _TEXTS[(worker_id + index) % len(_TEXTS)]
        writer.writerow((text, f"capacity-{worker_id}-{index}"))
    return output.getvalue().encode("utf-8")


def _run_csv_batches(
    base_url: str, token: str, expected_model_version: str, concurrency: int,
    row_count: int, timeout_seconds: float, verify: str, suite_start: float,
) -> dict:
    def submit_and_wait(worker_id: int) -> dict:
        started = time.monotonic()
        result = {
            "worker_id": worker_id, "row_count_expected": row_count,
            "batch_id": None,
            "upload_acceptance_latency_ms": None, "terminal_status_latency_ms": None,
            "completion_latency_ms": None,
            "row_count_returned": 0, "succeeded": 0, "failed": 0, "pending": 0,
            "model_version_mismatches": 0, "error": None,
            "worker_error_codes": [], "worker_error_reasons": [],
        }
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(
            base_url=base_url, headers=headers, timeout=httpx.Timeout(20.0, connect=5.0),
            verify=verify,
        trust_env=False,
        ) as client:
            try:
                uploaded = client.post(
                    "/v1/batches",
                    files={"file": (f"capacity-{worker_id}.csv", _benchmark_csv(row_count, worker_id), "text/csv")},
                )
                result["upload_acceptance_latency_ms"] = round((time.monotonic() - started) * 1000, 3)
                if uploaded.status_code != 202:
                    result["error"] = f"upload_http_{uploaded.status_code}"
                    return result
                batch_id = uploaded.json().get("id")
                if not batch_id:
                    result["error"] = "upload_missing_batch_id"
                    return result
                result["batch_id"] = batch_id
                deadline = started + timeout_seconds
                batch = {}
                while time.monotonic() < deadline:
                    response = client.get(f"/v1/batches/{batch_id}")
                    if response.status_code != 200:
                        result["error"] = f"poll_http_{response.status_code}"
                        return result
                    batch = response.json()
                    if batch.get("status") in {"completed", "failed", "paused"}:
                        break
                    time.sleep(1.0)
                result.update({
                    "batch_status": batch.get("status"),
                    "succeeded": int(batch.get("succeeded", 0)),
                    "failed": int(batch.get("failed", 0)),
                    "pending": int(batch.get("pending", 0)),
                    "terminal_status_latency_ms": round((time.monotonic() - started) * 1000, 3),
                })
                if batch.get("status") == "completed":
                    result["completion_latency_ms"] = result["terminal_status_latency_ms"]
                downloaded = client.get(f"/v1/batches/{batch_id}/download")
                if downloaded.status_code != 200:
                    result["error"] = f"download_http_{downloaded.status_code}"
                    return result
                rows = list(csv.DictReader(io.StringIO(downloaded.text)))
                result["row_count_returned"] = len(rows)
                result["worker_error_codes"] = sorted({
                    row["error_code"] for row in rows if row.get("error_code")
                })
                result["worker_error_reasons"] = sorted({
                    row["error_reason"] for row in rows if row.get("error_reason")
                })
                result["model_version_mismatches"] = sum(
                    bool(row.get("model_version"))
                    and row.get("model_version") != expected_model_version
                    for row in rows
                )
                if batch.get("status") != "completed":
                    result["error"] = f"batch_not_completed:{batch.get('status') or 'unknown'}"
                    return result
                if (
                    len(rows) != row_count or result["succeeded"] != row_count
                    or result["failed"] != 0 or result["pending"] != 0
                    or result["model_version_mismatches"] != 0
                    or any(row.get("row_status") != "succeeded" for row in rows)
                ):
                    result["error"] = "batch_rows_or_model_versions_invalid"
            except httpx.HTTPError as exc:
                result["error"] = "http_error:" + type(exc).__name__
        return result

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        batches = list(pool.map(submit_and_wait, range(concurrency)))
    ended = time.monotonic()
    upload_values = [float(batch["upload_acceptance_latency_ms"]) for batch in batches if batch["upload_acceptance_latency_ms"] is not None]
    completion_values = [float(batch["completion_latency_ms"]) for batch in batches if batch["completion_latency_ms"] is not None]
    rows_completed = sum(int(batch["succeeded"]) for batch in batches)
    errors = [batch for batch in batches if batch["error"]]
    return {
        "concurrency": concurrency,
        "start_offset_seconds": round(started - suite_start, 3),
        "end_offset_seconds": round(ended - suite_start, 3),
        "rows_per_file": row_count,
        "batch_count": len(batches),
        "total_expected_rows": row_count * len(batches),
        "total_successful_rows": rows_completed,
        "row_completion_throughput_per_second": round(rows_completed / (ended - started), 3) if ended > started else None,
        "upload_acceptance_latency_ms": _percentiles(upload_values),
        "end_to_end_batch_completion_latency_ms": _percentiles(completion_values),
        "model_version_mismatches": sum(int(batch["model_version_mismatches"]) for batch in batches),
        "errors": len(errors),
        "correctness_pass": not errors and rows_completed == row_count * len(batches),
        "numeric_performance_gate": "not frozen for CSV/worker; metrics are reported only",
        "batches": batches,
    }


def _evaluate(gate: dict, candidate: dict, results: list[dict], soak: dict, csv_result: dict,
              aggregate_resources: dict, initial_states: dict, final_states: dict,
              sampling_status: str) -> dict:
    steady_concurrency = int(gate["load_profile"].get("steady_concurrency", 8))
    burst_concurrency = int(gate["load_profile"].get("burst", {}).get("concurrency", 16))
    steady = next(result for result in results if result["concurrency"] == steady_concurrency)
    burst = next(result for result in results if result["concurrency"] == burst_concurrency)
    attempts = [*results, soak]
    total_requests = sum(int(item["attempts"]) for item in attempts)
    successful = sum(int(item["successful_requests"]) for item in attempts)
    errors = total_requests - successful
    mismatches = sum(int(item.get("errors", {}).get("model_version_mismatch", 0)) for item in attempts)
    limits = gate["acceptance"]
    def meets_max(value, maximum):
        return value is not None and value <= maximum

    def meets_min(value, minimum):
        return value is not None and value >= minimum

    candidate_type_role = {"xlmr-c3d15da2f4d2": "primary", "tfidf-3fb0efe32633": "fallback"}
    memory_gib = (
        aggregate_resources["peak_aggregate_container_memory_bytes"] / 1024**3
        if aggregate_resources["peak_aggregate_container_memory_bytes"] is not None else None
    )
    checks = {
        "candidate_matches_gate": candidate["model_version"] in gate["scope"]["candidate_versions"] and candidate["role"] == candidate_type_role.get(candidate["model_version"]),
        "steady_successful_throughput": meets_min(steady["successful_requests_per_second"], limits["steady_state"]["successful_throughput_requests_per_second_min"]),
        "steady_p95": meets_max(steady["all_attempt_latency_ms"]["p95_ms"], limits["steady_state"]["p95_latency_ms_max"]),
        "steady_p99": meets_max(steady["all_attempt_latency_ms"]["p99_ms"], limits["steady_state"]["p99_latency_ms_max"]),
        "burst_p95": meets_max(burst["all_attempt_latency_ms"]["p95_ms"], limits["burst"]["p95_latency_ms_max"]),
        "burst_p99": meets_max(burst["all_attempt_latency_ms"]["p99_ms"], limits["burst"]["p99_latency_ms_max"]),
        "soak_throughput": meets_min(soak["successful_requests_per_second"], limits["steady_state"]["successful_throughput_requests_per_second_min"]),
        "soak_p95": meets_max(soak["all_attempt_latency_ms"]["p95_ms"], limits["steady_state"]["p95_latency_ms_max"]),
        "soak_p99": meets_max(soak["all_attempt_latency_ms"]["p99_ms"], limits["steady_state"]["p99_latency_ms_max"]),
        "minimum_successful_requests": successful >= gate["load_profile"]["minimum_successful_valid_classification_requests_per_candidate"],
        "valid_request_error_rate": (errors / total_requests if total_requests else 1.0) <= limits["valid_request_error_rate_max"],
        "zero_model_version_mismatches": mismatches == 0 and csv_result["model_version_mismatches"] == 0,
        "csv_worker_correctness": csv_result["correctness_pass"],
        "aggregate_memory": memory_gib is not None and memory_gib <= limits["resources"]["aggregate_container_memory_gib_max"],
        "aggregate_cpu_average": aggregate_resources["average_aggregate_container_cpu_core_equivalents"] is not None and aggregate_resources["average_aggregate_container_cpu_core_equivalents"] <= limits["resources"]["aggregate_container_cpu_average_core_equivalents_max"],
        "aggregate_cpu_peak": aggregate_resources["peak_aggregate_container_cpu_core_equivalents"] is not None and aggregate_resources["peak_aggregate_container_cpu_core_equivalents"] <= limits["resources"]["aggregate_container_cpu_peak_core_equivalents_max"],
        "all_six_services_sampled": sampling_status == "complete",
        "zero_oom_and_restarts": all(
            final_states[role]["status"] == "running"
            and not final_states[role]["oom_killed"]
            and final_states[role]["restart_count"] == initial_states[role]["restart_count"]
            for role in _CONTAINER_ROLES
        ),
    }
    return {
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "classification_requests": {
            "attempts": total_requests,
            "successful": successful,
            "errors": errors,
            "error_rate": round(errors / total_requests, 6) if total_requests else None,
            "model_version_mismatches": mismatches,
        },
        "aggregate_container_peak_memory_gib": round(memory_gib, 3) if memory_gib is not None else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--lan-base-url", required=True)
    parser.add_argument("--ca-bundle", required=True, type=Path)
    parser.add_argument("--gate", required=True, type=Path)
    parser.add_argument("--candidate-role", choices=("primary", "fallback"), required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--benchmark-id", required=True)
    parser.add_argument("--api-container", required=True)
    parser.add_argument("--postgres-container", required=True)
    parser.add_argument("--redis-container", required=True)
    parser.add_argument("--proxy-container", required=True)
    parser.add_argument("--web-container", required=True)
    parser.add_argument("--worker-container", required=True)
    parser.add_argument("--batch-concurrency", type=int, default=8)
    parser.add_argument("--batch-rows", type=int, default=100)
    parser.add_argument("--batch-timeout-seconds", type=int, default=1800)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    parsed = urlparse(args.base_url)
    lan_parsed = urlparse(args.lan_base_url)
    if parsed.scheme != "https" or parsed.hostname != "localhost":
        parser.error("the primary frozen capacity run must use the localhost HTTPS origin")
    if lan_parsed.scheme != "https" or not lan_parsed.hostname or lan_parsed.hostname == "localhost":
        parser.error("the second origin must use the configured LAN HTTPS hostname or address")
    if not args.ca_bundle.is_file() or not args.gate.is_file() or not args.manifest.is_file():
        parser.error("CA bundle, gate, and candidate manifest must exist")
    if args.output.exists():
        parser.error(f"refusing to overwrite existing report: {args.output}")
    gate = json.loads(args.gate.read_text(encoding="utf-8"))
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    role = args.candidate_role
    model_version = manifest.get("version")
    expected_type = {"primary": "xlmr", "fallback": "tfidf"}[role]
    if manifest.get("model_type") != expected_type or model_version not in gate["scope"]["candidate_versions"]:
        parser.error("manifest version or model role does not match the frozen gate")
    if re.fullmatch(r"[0-9a-f]{32}", args.benchmark_id) is None:
        parser.error("benchmark id must be a random 32-character lowercase hexadecimal nonce")

    verify = str(args.ca_bundle.resolve())
    container_names = {
        "proxy": args.proxy_container, "web": args.web_container,
        "api": args.api_container, "worker": args.worker_container,
        "postgres": args.postgres_container, "redis": args.redis_container,
    }
    try:
        expected_project = f"invoiceops-capacity-full-{args.benchmark_id}"
        initial_states = _container_states(container_names, expected_project)
        resource_limits = {role_name: _container_config(name) for role_name, name in container_names.items()}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        parser.error(f"could not verify the six isolated containers: {type(exc).__name__}")
    if any(state["status"] != "running" for state in initial_states.values()):
        parser.error("all six isolated capacity services must be running before load starts")

    with httpx.Client(base_url=args.base_url, timeout=10.0, verify=verify, trust_env=False) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")
        web_health = client.get("/_stcore/health")
        web_home = client.get("/")
        if any(response.status_code != 200 for response in (health, ready, web_health, web_home)):
            parser.error("HTTPS proxy, API readiness, and Web smoke checks must pass before load")
        if ready.json().get("model_version") != model_version:
            parser.error("ready API model version differs from the supplied manifest")
        identity = client.get(
            "/_benchmark/identity",
            headers={"X-InvoiceOps-Benchmark-ID": args.benchmark_id},
        )
        if identity.status_code != 200:
            parser.error("target does not expose the isolated benchmark identity")
        identity_body = identity.json()
        if not _matches_benchmark_identity(identity_body, args.benchmark_id, model_version):
            parser.error("target benchmark identity does not match this isolated run")
        if identity_body.get("candidate_role") != role:
            parser.error("target benchmark candidate role differs from the frozen gate run")
        email = f"capacity-{uuid4().hex}@example.test"
        password = "CapacityBenchmark2026"
        registered = client.post("/v1/auth/register", json={"email": email, "password": password})
        if registered.status_code != 200:
            parser.error(f"unable to register disposable benchmark account (status {registered.status_code})")
        login = client.post("/v1/auth/login", json={"email": email, "password": password})
        if login.status_code != 200:
            parser.error(f"unable to authenticate disposable benchmark account (status {login.status_code})")
        token = login.json()["token"]

    with httpx.Client(base_url=args.lan_base_url, timeout=10.0, verify=verify, trust_env=False) as lan_client:
        lan_checks = {
            "/healthz": lan_client.get("/healthz").status_code,
            "/readyz": lan_client.get("/readyz").status_code,
            "/_stcore/health": lan_client.get("/_stcore/health").status_code,
            "/": lan_client.get("/").status_code,
        }
        lan_checks["/_benchmark/identity"] = lan_client.get(
            "/_benchmark/identity",
            headers={"X-InvoiceOps-Benchmark-ID": args.benchmark_id},
        ).status_code
        if any(status != 200 for status in lan_checks.values()):
            parser.error("the second LAN HTTPS origin smoke checks must pass before load")

    stop = threading.Event()
    samples: list[dict] = []
    monitor_errors: list[str] = []
    suite_start = time.monotonic()
    monitor = threading.Thread(
        target=_monitor_containers,
        args=(container_names, stop, samples, monitor_errors), daemon=True,
    )
    monitor.start()
    warmup = float(gate["load_profile"]["warmup_seconds_per_level"])
    duration = float(gate["load_profile"]["measurement_seconds_per_level"])
    results = [
        _run_level(args.base_url, token, concurrency, warmup, duration, suite_start, model_version, verify)
        for concurrency in gate["load_profile"]["concurrency_levels"]
    ]
    soak_conf = gate["load_profile"]["soak"]
    soak = _run_level(
        args.base_url, token, int(soak_conf["concurrency"]), warmup,
        float(soak_conf["duration_seconds"]), suite_start, model_version, verify,
    )
    csv_result = _run_csv_batches(
        args.base_url, token, model_version, args.batch_concurrency,
        args.batch_rows, args.batch_timeout_seconds, verify, suite_start,
    )
    stop.set()
    monitor.join(timeout=30)
    if monitor.is_alive():
        monitor_errors.append("resource monitor did not stop")
    final_states = _container_states(container_names, expected_project)
    suite_elapsed = time.monotonic() - suite_start
    for sample in samples:
        sample["sample_offset_seconds"] = sample["sample_monotonic"] - suite_start
    resource_sampling = _resource_sampling_summary(samples, _CONTAINER_ROLES)
    resource_sampling["container_set_snapshot_errors"] = monitor_errors
    measurement_windows = [
        (item["measurement_start_offset_seconds"], item["completion_end_offset_seconds"])
        for item in [*results, soak]
    ]
    measurement_windows.append((csv_result["start_offset_seconds"], csv_result["end_offset_seconds"]))
    aggregate_resources = _aggregate_resources(samples, measurement_windows)
    resource_sampling["status"] = "complete" if (
        resource_sampling["status"] == "complete"
        and aggregate_resources["complete_snapshot_count"] > 1
        and not monitor_errors
    ) else "incomplete"
    gate_result = _evaluate(
        gate, {"model_version": model_version, "role": role}, results, soak,
        csv_result, aggregate_resources, initial_states, final_states,
        resource_sampling["status"],
    )
    report = {
        "schema_version": 1,
        "benchmark_type": gate.get("benchmark_type", "frozen_six_service_https_engineering_capacity"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "gate_id": gate["gate_id"],
        "gate_sha256": hashlib.sha256(args.gate.read_bytes()).hexdigest(),
        "benchmark_id": args.benchmark_id,
        "service": "isolated six-service Compose stack accessed through HTTPS proxy",
        "candidate": {
            "role": role,
            "model_version": model_version,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "engineering_approved": False,
        },
        "environment": _environment_snapshot(),
        "source_sha256": _source_hashes(),
        "target_profile": {
            "compose_file": "compose.capacity.full.yaml",
            "model_device": "cpu",
            "access_origin": args.base_url,
            "lan_origin": args.lan_base_url,
            "tls_validation": "both HTTPS origins verified against the existing Caddy root certificate",
            "services": list(_CONTAINER_ROLES),
            "database_and_queue": "disposable PostgreSQL/Redis Compose volumes",
        },
        "input_policy": "benchmark-only Chinese/English/mixed text and CSV; no business data read",
        "load_profile": {
            "concurrency_levels": gate["load_profile"]["concurrency_levels"],
            "warmup_seconds_per_level": warmup,
            "measurement_seconds_per_level": duration,
            "soak_concurrency": soak_conf["concurrency"],
            "soak_seconds": soak_conf["duration_seconds"],
            "latency_percentile_method": "nearest_rank",
            "closed_loop_workers": True,
        },
        "resource_limits": resource_limits,
        "initial_container_states": initial_states,
        "final_container_states": final_states,
        "resource_sampling": resource_sampling,
        "access_origin_checks": {
            "localhost_https": {"base_url": args.base_url, "smoke_checks_passed": True},
            "lan_https_from_benchmark_host": {"base_url": args.lan_base_url, "status_codes": lan_checks},
            "second_windows_device_user_confirmation": "not included; requires a client-side check during this isolated run",
        },
        "aggregate_resources": aggregate_resources,
        "total_elapsed_seconds": round(suite_elapsed, 3),
        "results": results,
        "soak_result": soak,
        "csv_worker_evaluation": csv_result,
        "gate_evaluation": gate_result,
        "approval_effect": "none; this isolated benchmark does not alter model artifacts, approval files, or production release records",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output), "candidate": report["candidate"], "gate_evaluation": gate_result}, ensure_ascii=False))
    if gate_result["status"] != "pass":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
