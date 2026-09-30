"""Run the real CSV worker against an unapproved candidate in an isolated benchmark only."""

from __future__ import annotations

import os
import logging
import re
from importlib import import_module
from pathlib import Path

from invoiceops.ml_runtime.supervisor import ModelSupervisor


_LOGGER = logging.getLogger(__name__)
_RUN_ID = os.getenv("INVOICEOPS_BENCHMARK_ID", "")
if os.getenv("INVOICEOPS_BENCHMARK_MODE") != "capacity-only-unapproved-candidate" or not re.fullmatch(r"[0-9a-f]{32}", _RUN_ID):
    raise RuntimeError("benchmark worker requires an isolated capacity run id and explicit benchmark mode")


class _BenchmarkSupervisor(ModelSupervisor):
    _require_engineering_approval = False


class _BenchmarkRuntime:
    """Give the batch classifier an isolated serving gate without approving the artifact."""

    def __init__(
        self,
        path: str,
        role: str,
        taxonomy_path: str | Path | None = None,
        device: str = "cpu",
        inference_timeout_seconds: float = 10.0,
        startup_timeout_seconds: float = 180.0,
    ) -> None:
        model_path = Path(path)
        if (model_path / "approval.json").exists():
            raise RuntimeError("capacity benchmark refuses a candidate with approval.json")
        self._supervisor = _BenchmarkSupervisor(
            model_path,
            role=role,
            taxonomy_path=taxonomy_path or os.getenv("INVOICEOPS_TAXONOMY", "configs/taxonomy.json"),
            device=device,
            inference_timeout_seconds=inference_timeout_seconds,
            startup_timeout_seconds=startup_timeout_seconds,
        )
        self._candidate_engineering_approved = False
        self.engineering_approved = True

    def __getattr__(self, name: str):
        return getattr(self._supervisor, name)

    @property
    def candidate_engineering_approved(self) -> bool:
        return self._supervisor.engineering_approved

    def start(self):
        try:
            self._supervisor.start()
        except Exception:
            _LOGGER.exception("capacity benchmark worker candidate startup failed")
            raise
        if self._supervisor.engineering_approved:
            self.close()
            raise RuntimeError("capacity benchmark requires an unapproved candidate")
        return self

    def predict_proba(self, texts):
        try:
            return self._supervisor.predict_proba(texts)
        except Exception:
            _LOGGER.exception("capacity benchmark worker candidate inference failed")
            raise

    def close(self):
        self._supervisor.close()


_tasks = import_module("apps.worker.tasks")

_tasks.ModelSupervisor = _BenchmarkRuntime
celery_app = _tasks.celery_app
