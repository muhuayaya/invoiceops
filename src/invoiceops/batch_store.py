from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from invoiceops.batch import BatchIdempotencyRecord, BatchJob, BatchStatus


class PersistentBatchStore:
    """Durable batch checkpoints for the local worker fallback."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS batch_jobs (
                  batch_id TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  total_rows INTEGER NOT NULL DEFAULT 0,
                  processed_rows INTEGER NOT NULL DEFAULT 0,
                  success_rows INTEGER NOT NULL DEFAULT 0,
                  failed_rows INTEGER NOT NULL DEFAULT 0,
                  row_errors TEXT NOT NULL DEFAULT '[]',
                  payload BLOB NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_idempotency (
                  key TEXT PRIMARY KEY,
                  fingerprint TEXT NOT NULL,
                  batch_id TEXT NOT NULL UNIQUE
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def create(self, content: bytes) -> BatchJob:
        job = BatchJob(batch_id=uuid4())
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO batch_jobs(batch_id,status,payload,created_at) VALUES(?,?,?,?)",
                (str(job.batch_id), job.status.value, content, job.created_at.isoformat()),
            )
        return job

    def find_batch_idempotency(self, key: str) -> BatchIdempotencyRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT fingerprint, batch_id FROM batch_idempotency WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return BatchIdempotencyRecord(row["fingerprint"], UUID(row["batch_id"]))

    def save_batch_idempotency(self, key: str, record: BatchIdempotencyRecord) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO batch_idempotency(key, fingerprint, batch_id) VALUES(?,?,?)",
                (key, record.fingerprint, str(record.batch_id)),
            )

    def get(self, batch_id: UUID) -> BatchJob | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM batch_jobs WHERE batch_id = ?", (str(batch_id),)).fetchone()
        if row is None:
            return None
        return BatchJob(
            batch_id=UUID(row["batch_id"]), status=BatchStatus(row["status"]), total_rows=row["total_rows"],
            processed_rows=row["processed_rows"], success_rows=row["success_rows"], failed_rows=row["failed_rows"],
            row_errors=json.loads(row["row_errors"]), created_at=datetime.fromisoformat(row["created_at"]),
        )

    def payload(self, batch_id: UUID) -> bytes:
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM batch_jobs WHERE batch_id = ?", (str(batch_id),)).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return bytes(row["payload"])

    def save(self, job: BatchJob) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE batch_jobs SET status=?,total_rows=?,processed_rows=?,success_rows=?,failed_rows=?,row_errors=? WHERE batch_id=?",
                (job.status.value, job.total_rows, job.processed_rows, job.success_rows, job.failed_rows, json.dumps(job.row_errors), str(job.batch_id)),
            )

    def recover_incomplete(self) -> list[UUID]:
        """Claim batches interrupted after a worker began processing them.

        Queued messages remain durable in Redis and must not be re-published on
        every worker startup. The transaction changes processing rows before
        returning them, so only the first concurrent recovery scan claims them.
        """
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT batch_id FROM batch_jobs WHERE status = 'processing'").fetchall()
            connection.execute("UPDATE batch_jobs SET status='queued' WHERE status='processing'")
        return [UUID(row["batch_id"]) for row in rows]
