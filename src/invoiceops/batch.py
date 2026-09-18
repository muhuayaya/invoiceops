from __future__ import annotations

import csv
import hashlib
import io
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from invoiceops.application.service import IdempotencyConflict, TriageService


class BatchStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BatchJob:
    batch_id: UUID
    status: BatchStatus = BatchStatus.QUEUED
    total_rows: int = 0
    processed_rows: int = 0
    success_rows: int = 0
    failed_rows: int = 0
    row_errors: list[dict[str, object]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class BatchIdempotencyRecord:
    fingerprint: str
    batch_id: UUID


class BatchProcessor:
    def __init__(self, service: TriageService, store=None) -> None:
        self.service = service
        self.store = store
        self.jobs: dict[UUID, BatchJob] = {}
        self.pending_payloads: dict[UUID, bytes] = {}
        self.idempotency: dict[str, BatchIdempotencyRecord] = {}

    @staticmethod
    def _fingerprint(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def create_with_result(self, content: bytes, idempotency_key: str | None = None) -> tuple[BatchJob, bool]:
        fingerprint = self._fingerprint(content)
        if idempotency_key:
            previous = self.store.find_batch_idempotency(idempotency_key) if self.store is not None else self.idempotency.get(idempotency_key)
            if previous:
                if previous.fingerprint != fingerprint:
                    raise IdempotencyConflict("idempotency key was already used with a different batch")
                existing = self.get(previous.batch_id)
                if existing is not None:
                    return existing, False

        if self.store is not None:
            job = self.store.create(content)
        else:
            batch_id = uuid4()
            job = BatchJob(batch_id=batch_id)
            self.jobs[batch_id] = job
            self.pending_payloads[batch_id] = content
        if idempotency_key:
            record = BatchIdempotencyRecord(fingerprint, job.batch_id)
            if self.store is not None:
                self.store.save_batch_idempotency(idempotency_key, record)
            else:
                self.idempotency[idempotency_key] = record
        return job, True

    def create(self, content: bytes, idempotency_key: str | None = None) -> BatchJob:
        return self.create_with_result(content, idempotency_key)[0]

    def process(self, batch_id: UUID) -> BatchJob:
        if self.store is not None:
            job = self.store.get(batch_id)
            if job is None:
                raise KeyError(batch_id)
            self._process_content(job, self.store.payload(batch_id))
            return job
        job = self.jobs[batch_id]
        content = self.pending_payloads.pop(batch_id, b"")
        self._process_content(job, content)
        return job

    def submit(self, content: bytes, idempotency_key: str | None = None) -> BatchJob:
        job = self.create(content, idempotency_key)
        return self.process(job.batch_id)

    def _process_content(self, job: BatchJob, content: bytes) -> None:
        try:
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            if not {"request_id", "source", "text", "taxonomy_version"} <= set(reader.fieldnames or {}):
                raise ValueError("CSV must contain request_id, source, text, taxonomy_version")
            max_rows = int(os.getenv("INVOICEOPS_MAX_BATCH_ROWS", "10000"))
            rows = []
            for row in reader:
                if len(rows) >= max_rows:
                    raise ValueError(f"CSV exceeds {max_rows} data rows")
                rows.append(row)
            job.total_rows = len(rows)
            job.status = BatchStatus.PROCESSING
            starting_processed = job.processed_rows
            # The pause is disabled by default and exists only to make the live
            # worker-crash test deterministic after a durable checkpoint.
            pause_after_checkpoint = float(os.getenv("INVOICEOPS_TEST_PAUSE_AFTER_CHECKPOINT_SECONDS", "0"))
            if self.store is not None:
                self.store.save(job)
            for row_number, row in enumerate(rows, start=2):
                if row_number - 1 <= starting_processed:
                    continue
                job.processed_rows += 1
                try:
                    if not row.get("request_id") or not row.get("text"):
                        raise ValueError("request_id and text are required")
                    if row.get("source") not in {"email", "portal", "api", "manual"}:
                        raise ValueError("source must be one of email, portal, api, manual")
                    if row.get("taxonomy_version") != self.service.taxonomy.version:
                        raise ValueError(f"unknown taxonomy version: {row.get('taxonomy_version', '')}")
                    channel = row.get("channel", "")
                    if channel and channel not in {"supplier_portal", "email", "internal"}:
                        raise ValueError("channel must be one of supplier_portal, email, internal")
                    self.service.classify(
                        request_id=row["request_id"], source=row.get("source", "api"), text=row["text"],
                        taxonomy_version=row.get("taxonomy_version", ""), metadata={"channel": row.get("channel", "")},
                        idempotency_key=f"batch:{job.batch_id}:{row_number}", trace_id=str(uuid4()),
                    )
                    job.success_rows += 1
                except Exception as exc:
                    job.failed_rows += 1
                    job.row_errors.append({"row_number": row_number, "code": type(exc).__name__, "message": str(exc)})
                if self.store is not None:
                    self.store.save(job)
                    if pause_after_checkpoint > 0:
                        time.sleep(pause_after_checkpoint)
            job.status = BatchStatus.COMPLETED if job.success_rows else BatchStatus.FAILED
        except Exception as exc:
            job.status = BatchStatus.FAILED
            job.row_errors.append({"row_number": 1, "code": type(exc).__name__, "message": str(exc)})
        if self.store is not None:
            self.store.save(job)

    def get(self, batch_id: UUID) -> BatchJob | None:
        if self.store is not None:
            return self.store.get(batch_id)
        return self.jobs.get(batch_id)


def as_dict(job: BatchJob) -> dict[str, object]:
    return {"batch_id": str(job.batch_id), "status": job.status.value, "total_rows": job.total_rows, "processed_rows": job.processed_rows, "success_rows": job.success_rows, "failed_rows": job.failed_rows, "row_errors": job.row_errors}
