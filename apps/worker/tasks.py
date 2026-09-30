"""Restart-safe CSV processing; each row commits atomically."""

from __future__ import annotations

import os
from functools import lru_cache

from celery import Celery
from celery.signals import worker_process_shutdown
from sqlalchemy import select

from invoiceops.adapters import db as store
from invoiceops.application.classifier import ModelUnavailable, classify
from invoiceops.data.contract import load_taxonomy
from invoiceops.ml_runtime.supervisor import ModelSupervisor


celery_app = Celery("invoiceops", broker=os.getenv("INVOICEOPS_REDIS_URL", "redis://redis:6379/0"))
celery_app.conf.beat_schedule = {
    "resume-incomplete-batches": {"task": "invoiceops.resume_batches", "schedule": 60.0},
}


_MODEL_SUPERVISORS: list[ModelSupervisor] = []


@lru_cache(maxsize=2)
def _runtime(path: str | None, role: str):
    if not path:
        return None
    try:
        runtime = ModelSupervisor(
            path, role=role,
            taxonomy_path=os.getenv("INVOICEOPS_TAXONOMY", "configs/taxonomy.json"),
            device=os.getenv("INVOICEOPS_MODEL_DEVICE", "cpu"),
            inference_timeout_seconds=float(os.getenv("INVOICEOPS_MODEL_INFERENCE_TIMEOUT_SECONDS", "10")),
            startup_timeout_seconds=float(os.getenv("INVOICEOPS_MODEL_STARTUP_TIMEOUT_SECONDS", "180")),
            allow_unapproved_demo=os.getenv("INVOICEOPS_DEMO_MODE", "false").strip().lower() == "true",
        )
        _MODEL_SUPERVISORS.append(runtime)
        return runtime
    except Exception:
        return None


@worker_process_shutdown.connect
def _close_model_supervisors(**_kwargs):
    for runtime in _MODEL_SUPERVISORS:
        runtime.close()
    _MODEL_SUPERVISORS.clear()
    _runtime.cache_clear()


def _database_engine():
    return store.get_engine(os.environ["INVOICEOPS_DATABASE_URL"])


@celery_app.task(name="invoiceops.process_batch")
def process_batch(batch_id: str):
    engine = _database_engine()
    taxonomy_version, labels = load_taxonomy(os.getenv("INVOICEOPS_TAXONOMY", "configs/taxonomy.json"))
    primary = _runtime(os.getenv("INVOICEOPS_PRIMARY_MODEL"), "primary")
    fallback = _runtime(os.getenv("INVOICEOPS_FALLBACK_MODEL"), "fallback")
    for runtime in (primary, fallback):
        if runtime is not None:
            try:
                runtime.start()
            except Exception:
                pass  # classify() reports an unavailable model and pauses the batch.
    with store.new_session(engine) as session:
        rows = list(session.scalars(select(store.BatchItem.row_number).where(store.BatchItem.batch_id == batch_id, store.BatchItem.status == "pending").order_by(store.BatchItem.row_number)))
    for row_number in rows:
        with store.new_session(engine) as session, session.begin():
            item = session.scalar(select(store.BatchItem).where(store.BatchItem.batch_id == batch_id, store.BatchItem.row_number == row_number).with_for_update(skip_locked=True))
            if item is None or item.status != "pending":
                continue
            batch = session.get(store.BatchJob, batch_id)
            if batch is None:
                return
            try:
                result = classify(item.redacted_text, primary=primary, fallback=fallback, label_order=labels, taxonomy_version=taxonomy_version)
            except ModelUnavailable:
                item.error_code = "model_unavailable"
                item.error_reason = "No approved model is currently available; this row will retry"
                batch.status = "paused"
                break
            except Exception:
                item.status = "failed"
                item.error_code = "processing_error"
                item.error_reason = "Unable to classify this row"
                continue
            key = f"batch:{batch_id}:{row_number}"
            ticket = store.create_ticket(session, batch.submitted_by, result.redacted_text, idempotency_key=key)
            prediction = session.scalar(select(store.Prediction.id).where(store.Prediction.ticket_id == ticket.id).limit(1))
            if prediction is None:
                store.add_prediction(session, ticket.id, probabilities=result.probabilities, labels=list(result.labels), model_version=result.model_version, threshold_version=result.threshold_version, taxonomy_version=result.taxonomy_version, degraded=result.degraded, review_reason="|".join(result.review_reasons) or None, fallback_reason=result.fallback_reason)
            item.ticket_id = ticket.id
            item.actual_model_version = result.model_version
            item.threshold_version = result.threshold_version
            item.degraded = result.degraded
            item.error_code = None
            item.error_reason = None
            item.status = "succeeded"
            batch.status = "running"
    with store.new_session(engine) as session, session.begin():
        batch = session.get(store.BatchJob, batch_id)
        if batch is None:
            return
        statuses = list(session.scalars(select(store.BatchItem.status).where(store.BatchItem.batch_id == batch_id)))
        if "pending" not in statuses:
            batch.status = "completed"
        elif batch.status != "paused":
            batch.status = "running"


@celery_app.task(name="invoiceops.resume_batches")
def resume_batches():
    engine = _database_engine()
    with store.new_session(engine) as session:
        batch_ids = list(session.scalars(select(store.BatchJob.id).where(store.BatchJob.status.in_(("pending", "running", "paused")))))
    for batch_id in batch_ids:
        process_batch.delay(batch_id)
