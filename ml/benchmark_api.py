"""Serve the real classification API with an unapproved candidate in an isolated bench only."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from invoiceops.ml_runtime.supervisor import ModelSupervisor


class _BenchmarkSupervisor(ModelSupervisor):
    """Permit loading an unapproved candidate only inside the benchmark app."""

    _require_engineering_approval = False


class _BenchmarkCandidate:
    """Wrap an isolated ModelSupervisor to serve an unapproved candidate for measurement.

    The wrapped supervisor retains engineering_approved=False. The outer serving flag
    only lets the normal API classifier exercise real process IPC in this benchmark app;
    it is not an approval and must never be passed to the production app.
    """

    def __init__(
        self, model_path: Path, taxonomy_path: Path, benchmark_id: str,
        role: str = "primary",
    ) -> None:
        if role not in {"primary", "fallback"}:
            raise ValueError("benchmark candidate role must be primary or fallback")
        self._supervisor = _BenchmarkSupervisor(
            model_path,
            role=role,
            taxonomy_path=taxonomy_path,
            device="cpu",
            inference_timeout_seconds=float(os.getenv("INVOICEOPS_MODEL_INFERENCE_TIMEOUT_SECONDS", "10")),
            startup_timeout_seconds=float(os.getenv("INVOICEOPS_MODEL_STARTUP_TIMEOUT_SECONDS", "180")),
        )
        self.benchmark_id = benchmark_id
        self.role = role
        self.labels: tuple[str, ...] = ()
        self.thresholds: tuple[float, ...] = ()
        self.version: str | None = None
        self.threshold_version: str | None = None

    @property
    def engineering_approved(self) -> bool:
        # This is only the classifier's serving gate for the isolated benchmark app.
        return True

    @property
    def candidate_engineering_approved(self) -> bool:
        return self._supervisor.engineering_approved

    @property
    def ready(self) -> bool:
        return self._supervisor.ready

    def start(self):
        self._supervisor.start()
        if self.candidate_engineering_approved:
            self.close()
            raise RuntimeError("capacity benchmark requires an unapproved candidate")
        self.labels = self._supervisor.labels
        self.thresholds = self._supervisor.thresholds
        self.version = self._supervisor.version
        self.threshold_version = self._supervisor.threshold_version
        return self

    def predict_proba(self, texts):
        return self._supervisor.predict_proba(texts)

    def close(self):
        self._supervisor.close()


def _create_app(
    model_path: Path, database_url: str, taxonomy_path: Path, benchmark_id: str,
    role: str = "primary",
):
    os.environ["INVOICEOPS_DATABASE_URL"] = database_url
    os.environ["INVOICEOPS_TAXONOMY"] = str(taxonomy_path)
    # Keep the production app's module-level default app model-less. The benchmark app
    # below receives its candidate explicitly and remains the only app served here.
    os.environ["INVOICEOPS_PRIMARY_MODEL"] = ""
    os.environ["INVOICEOPS_FALLBACK_MODEL"] = ""

    if (model_path / "approval.json").exists():
        raise ValueError("capacity benchmark refuses a candidate with approval.json")
    manifest = json.loads((model_path / "manifest.json").read_text(encoding="utf-8"))
    required_type = {"primary": "xlmr", "fallback": "tfidf"}.get(role)
    if required_type is None:
        raise ValueError("benchmark candidate role must be primary or fallback")
    if manifest.get("model_type") != required_type:
        raise ValueError(f"the {role} benchmark requires a {required_type} candidate")
    if not benchmark_id:
        raise ValueError("benchmark id is required")

    from invoiceops.adapters.db import get_engine

    engine = get_engine(database_url)
    from apps.api.main import app as default_app, create_app

    # The default module app may be unconfigured when imported by tests or tools.
    default_engine = getattr(default_app.state, "engine", None)
    if default_engine is not None:
        default_engine.dispose()
    candidate = _BenchmarkCandidate(model_path, taxonomy_path, benchmark_id, role)
    app = create_app(
        engine=engine,
        primary=candidate if role == "primary" else None,
        fallback=candidate if role == "fallback" else None,
    )
    app.title = "InvoiceOps isolated API capacity benchmark"

    from fastapi import Header, HTTPException

    def benchmark_identity(x_invoiceops_benchmark_id: str | None = Header(default=None)):
        if x_invoiceops_benchmark_id != benchmark_id:
            raise HTTPException(status_code=404, detail="Not found")
        return {
            "benchmark_id": benchmark_id,
            "model_version": candidate.version,
            "candidate_role": role,
            "candidate_engineering_approved": candidate.candidate_engineering_approved,
        }

    app.add_api_route(
        "/_benchmark/identity",
        benchmark_identity,
        methods=["GET"],
        include_in_schema=False,
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="/bench-model", help="read-only candidate bundle")
    parser.add_argument("--taxonomy", default="/app/configs/taxonomy.json")
    parser.add_argument("--database-url", default=os.getenv("INVOICEOPS_DATABASE_URL"), required=not bool(os.getenv("INVOICEOPS_DATABASE_URL")))
    parser.add_argument("--benchmark-id", default=os.getenv("INVOICEOPS_BENCHMARK_ID"), required=not bool(os.getenv("INVOICEOPS_BENCHMARK_ID")))
    parser.add_argument("--role", choices=("primary", "fallback"), default="primary")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--benchmark-only-unapproved-candidate",
        action="store_true",
        help="required explicit acknowledgement; does not grant or persist model approval",
    )
    args = parser.parse_args()
    if not args.benchmark_only_unapproved_candidate:
        parser.error("this isolated app requires --benchmark-only-unapproved-candidate")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    import uvicorn

    app = _create_app(Path(args.model), args.database_url, Path(args.taxonomy), args.benchmark_id, args.role)
    print(
        f"BENCHMARK ONLY: run_id={args.benchmark_id}; candidate_engineering_approved=false; "
        f"unapproved {args.role} candidate is loaded through ModelSupervisor IPC; no approval file is written.",
        flush=True,
    )
    uvicorn.run(app, host="0.0.0.0", port=args.port, workers=1, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
