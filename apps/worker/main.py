"""Celery-compatible worker entry point with a local fallback."""

import os
from uuid import uuid4

from invoiceops.observability.logging import configure_logging

try:
    from celery import Celery
    from celery.signals import worker_ready
except ImportError:  # pragma: no cover - optional in a local test-only install
    Celery = None
    worker_ready = None

celery_app = Celery("invoiceops") if Celery else None
logger = configure_logging("invoiceops.worker")
if celery_app:
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    celery_app.conf.update(task_acks_late=True, task_reject_on_worker_lost=True, broker_url=redis_url, result_backend=redis_url)


if celery_app:
    @celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=1)
    def process_batch(self, batch_id: str, trace_id: str | None = None) -> dict[str, str]:
        trace_id = trace_id or str(uuid4())
        from invoiceops.application.service import TriageService
        from invoiceops.adapters.in_memory import InMemoryRepository
        from invoiceops.adapters.postgres import PostgresRepository
        from invoiceops.adapters.sqlite import SQLiteRepository
        from invoiceops.batch import BatchProcessor
        from invoiceops.batch_store import PersistentBatchStore

        store_path = os.getenv("INVOICEOPS_BATCH_DB", "/data/invoiceops-batches.sqlite3")
        data_path = os.getenv("INVOICEOPS_DATA_DB", "").strip()
        database_url = os.getenv("DATABASE_URL", "").strip()
        if database_url:
            repository = PostgresRepository(database_url)
        else:
            repository = SQLiteRepository(data_path) if data_path else InMemoryRepository()
        BatchProcessor(TriageService(repository=repository), PersistentBatchStore(store_path)).process(__import__("uuid").UUID(batch_id))
        logger.info("batch processing requested", extra={"trace_id": trace_id, "version": "worker-v1"})
        return {"batch_id": batch_id, "trace_id": trace_id}


    @worker_ready.connect
    def recover_incomplete_batches(sender=None, **_kwargs) -> None:
        """Re-enqueue durable jobs interrupted after a worker began processing."""
        from invoiceops.batch_store import PersistentBatchStore

        store_path = os.getenv("INVOICEOPS_BATCH_DB", "/data/invoiceops-batches.sqlite3")
        store = PersistentBatchStore(store_path)
        recovered = store.recover_incomplete()
        for batch_id in recovered:
            process_batch.delay(str(batch_id), f"recovery-{uuid4()}")
        logger.info(
            "incomplete batches recovered",
            extra={"trace_id": f"recovery-{uuid4()}", "version": "worker-v1", "recovered_count": len(recovered)},
        )


def run_once(trace_id: str | None = None) -> None:
    """Worker process hook; API tests use the deterministic local processor."""
    logger.info("worker ready", extra={"trace_id": trace_id or str(uuid4()), "version": "worker-v1"})
    return None


if __name__ == "__main__":
    run_once()
