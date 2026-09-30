"""Benchmark one unapproved model candidate with dev texts only.

Run each model in a separate process so process memory is attributable to one
model. Loading and warmup are excluded from latency measurements.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import time
from pathlib import Path

import numpy as np

from invoiceops.ml_runtime import load_model
from invoiceops.ml_runtime.runtime import _sha256


def _memory_bytes() -> dict[str, int | None]:
    if os.name != "nt":
        try:
            import resource

            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return {"current_working_set": None, "peak_working_set": int(peak * 1024)}
        except ImportError:
            return {"current_working_set": None, "peak_working_set": None}
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD,
    ]
    ctypes.windll.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
    return {"current_working_set": int(counters.WorkingSetSize),
            "peak_working_set": int(counters.PeakWorkingSetSize)}


def _latencies(model, texts: list[str], *, request_count: int, batch_size: int,
               warmup: int, device: str) -> dict:
    if device == "cuda":
        import torch

        def synchronize():
            torch.cuda.synchronize()
    else:
        def synchronize():
            return None

    for i in range(warmup):
        start = (i * batch_size) % len(texts)
        batch = [texts[(start + j) % len(texts)] for j in range(batch_size)]
        model.predict_proba(batch)
    synchronize()
    durations = []
    began = time.perf_counter()
    for i in range(request_count):
        start = (i * batch_size) % len(texts)
        batch = [texts[(start + j) % len(texts)] for j in range(batch_size)]
        synchronize()
        before = time.perf_counter()
        result = model.predict_proba(batch)
        synchronize()
        durations.append((time.perf_counter() - before) * 1000)
        if len(result) != batch_size:
            raise RuntimeError("Incorrect batch output count")
    elapsed = time.perf_counter() - began
    return {
        "requests": request_count, "texts_processed": request_count * batch_size,
        "batch_size": batch_size, "concurrency": 1,
        "p50_ms": float(np.percentile(durations, 50)),
        "p95_ms": float(np.percentile(durations, 95)),
        "p99_ms": float(np.percentile(durations, 99)),
        "throughput_texts_per_second": request_count * batch_size / elapsed,
        "total_wall_seconds": elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--role", required=True, choices=("primary", "fallback"))
    parser.add_argument("--dev", required=True)
    parser.add_argument("--taxonomy", default="configs/taxonomy.json")
    parser.add_argument("--device", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--single-requests", type=int, default=200)
    parser.add_argument("--batch-requests", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if min(args.single_requests, args.batch_requests, args.batch_size, args.warmup) < 1:
        parser.error("request counts, batch size, and warmup must be positive")
    manifest_path = Path(args.model) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dev_path = Path(args.dev)
    dev_hash = _sha256(dev_path)
    if dev_hash != manifest["dataset_manifest"]["splits"]["dev"]["sha256"]:
        parser.error("dev file hash differs from trained candidate")
    with dev_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"text", "language", "source"}.issubset(reader.fieldnames or []):
            parser.error("dev text, language, or source column missing")
        rows = list(reader)
    texts = [row["text"] for row in rows]
    if not texts or any(not text.strip() for text in texts):
        parser.error("dev text is empty")
    memory_before = _memory_bytes()
    loaded_at = time.perf_counter()
    model = load_model(args.model, role=args.role, taxonomy_path=args.taxonomy,
                       require_engineering_approval=False, device=args.device)
    load_seconds = time.perf_counter() - loaded_at
    if args.device == "cuda":
        import torch

        torch.cuda.reset_peak_memory_stats()
    single = _latencies(model, texts, request_count=args.single_requests,
                        batch_size=1, warmup=args.warmup, device=args.device)
    batch = _latencies(model, texts, request_count=args.batch_requests,
                       batch_size=args.batch_size, warmup=args.warmup, device=args.device)
    memory_after = _memory_bytes()
    gpu = None
    if args.device == "cuda":
        gpu = {
            "name": torch.cuda.get_device_name(),
            "total_memory_bytes": int(torch.cuda.get_device_properties(0).total_memory),
            "peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            "peak_reserved_bytes": int(torch.cuda.max_memory_reserved()),
            "torch_version": torch.__version__,
            "cuda_version": torch.version.cuda,
        }
    report = {
        "evidence_level": "engineering_candidate_dev_only",
        "engineering_approved": model.engineering_approved,
        "model_version": model.version, "model_type": model.model_type,
        "model_manifest_sha256": _sha256(manifest_path), "dev_sha256": dev_hash,
        "dev_rows": len(texts),
        "dev_languages": {language: sum(row["language"] == language for row in rows)
                          for language in sorted({row["language"] for row in rows})},
        "character_length_percentiles": {str(p): float(np.percentile([len(t) for t in texts], p))
                                          for p in (50, 95, 99)},
        "measurement": "serial requests; model load excluded; CUDA synchronized; repeated dev texts",
        "warmup_requests_per_mode": args.warmup,
        "load_seconds": load_seconds,
        "single": single, "batch": batch,
        "memory_bytes": {"before_load": memory_before, "after_benchmark": memory_after,
                         "gpu": gpu},
        "environment": {
            "platform": platform.platform(), "python": platform.python_version(),
            "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
            "device": args.device, "numpy": np.__version__,
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "single_p99_ms": single["p99_ms"],
                      "batch_p99_ms": batch["p99_ms"],
                      "single_tps": single["throughput_texts_per_second"],
                      "batch_tps": batch["throughput_texts_per_second"]}))


if __name__ == "__main__":
    main()
