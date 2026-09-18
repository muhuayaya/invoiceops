"""Small reproducible local performance check for the PoC acceptance gate."""
from __future__ import annotations

import csv
import io
import json
import statistics
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from invoiceops.application.service import TriageService
from invoiceops.batch import BatchProcessor


def main() -> None:
    service = TriageService()
    latencies: list[float] = []
    sample = "x" * (5000 - len(" payment status")) + " payment status"
    def run_one(index: int) -> float:
        started = time.perf_counter()
        service.classify(
            request_id=f"perf-{index}", source="api", text=sample,
            taxonomy_version="invoiceops-v1", metadata={}, idempotency_key=f"perf-key-{index}",
            trace_id=f"perf-trace-{index}",
        )
        return (time.perf_counter() - started) * 1000

    started_run = time.perf_counter()
    cpu_started = time.process_time()
    tracemalloc.start()
    with ThreadPoolExecutor(max_workers=5) as executor:
        latencies = list(executor.map(run_one, range(25)))
    elapsed_run = time.perf_counter() - started_run
    cpu_elapsed = time.process_time() - cpu_started
    _, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["request_id", "source", "text", "taxonomy_version"])
    for index in range(10_000):
        writer.writerow([f"batch-perf-{index}", "api", "Payment status?", "invoiceops-v1"])
    batch_started = time.perf_counter()
    batch = BatchProcessor(TriageService()).submit(output.getvalue().encode())
    batch_elapsed = (time.perf_counter() - batch_started) * 1000

    report = {
        "report_version": "performance-v1",
        "status": "local_poc_measurement",
        "environment": {"python": "current interpreter", "hardware": "not pinned", "model": "deterministic fallback"},
        "single_request": {"count": len(latencies), "target_qps": 5, "concurrency": 5, "observed_qps": len(latencies) / elapsed_run, "max_text_chars": len(sample), "p50_ms": statistics.median(latencies), "p95_ms": sorted(latencies)[int(len(latencies) * 0.95) - 1], "error_rate": 0.0},
        "batch_10000": {"rows": batch.total_rows, "success_rows": batch.success_rows, "failed_rows": batch.failed_rows, "elapsed_ms": batch_elapsed, "error_rate": batch.failed_rows / max(batch.total_rows, 1)},
        "resource_usage": {"python_cpu_seconds": cpu_elapsed, "peak_tracemalloc_mb": peak_memory / (1024 * 1024), "os_container_metrics": "not collected"},
        "gates": {"single_request_5qps": len(latencies) / elapsed_run >= 5, "single_request_p95_800ms": max(latencies) <= 800, "text_5000_chars": len(sample) <= 5000, "batch_10000_rows": batch.total_rows == 10_000, "production_ready": False},
    }
    path = Path("docs/performance/performance-report-v1.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
