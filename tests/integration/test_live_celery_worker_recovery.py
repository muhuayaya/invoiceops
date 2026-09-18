"""Live Redis/Celery recovery coverage, enabled by the CI Redis service."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import pytest
from celery import Celery
from redis import Redis

from invoiceops.adapters.sqlite import SQLiteRepository
from invoiceops.adapters.postgres import PostgresRepository
from invoiceops.batch import BatchStatus
from invoiceops.batch_store import PersistentBatchStore


pytestmark = pytest.mark.skipif(
    os.getenv("INVOICEOPS_RUN_LIVE_REDIS", "false").lower() != "true",
    reason="live Redis/Celery verification is enabled only in the Redis-backed CI job",
)


def _start_worker(env: dict[str, str], hostname: str, log_path: Path) -> tuple[subprocess.Popen[bytes], BinaryIO]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("wb")
    worker = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "apps.worker.main:celery_app",
            "worker",
            "--pool=solo",
            "--concurrency=1",
            "--hostname",
            hostname,
            "--loglevel=INFO",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    return worker, log_file


def _stop_worker(worker: subprocess.Popen[bytes], log_file: BinaryIO, *, force: bool = False) -> None:
    if worker.poll() is None:
        if force:
            worker.kill()
        else:
            worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=15)
    log_file.flush()
    log_file.close()


def _wait_worker_ready(redis_url: str, worker: subprocess.Popen[bytes], hostname: str) -> None:
    probe = Celery("invoiceops-readiness-probe", broker=redis_url)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert worker.poll() is None, "the Celery worker exited before becoming ready"
        # Celery 5 prefixes short hostnames with ``celery@`` on Windows.
        # Probe both forms so readiness is independent of the worker platform.
        response = probe.control.inspect(
            destination=[hostname, f"celery@{hostname}"], timeout=1
        ).ping()
        if response and any(name in response for name in (hostname, f"celery@{hostname}")):
            return
        time.sleep(0.5)
    raise AssertionError("Celery worker did not answer a readiness ping")


def test_real_worker_restart_recovers_without_duplicate_records(tmp_path: Path) -> None:
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    broker = Redis.from_url(redis_url)
    assert broker.ping()

    batch_path = tmp_path / "batches.sqlite3"
    data_path = tmp_path / "business.sqlite3"
    run_id = uuid4().hex[:8]
    rows = [
        f"live-restart-{run_id}-{index},email,Payment status?,invoiceops-v1"
        for index in range(1, 4)
    ]
    expected_request_ids = {row.split(",", 1)[0] for row in rows}
    content = ("request_id,source,text,taxonomy_version\n" + "\n".join(rows) + "\n").encode()
    database_url = os.getenv("DATABASE_URL", "").strip()
    base_env = os.environ.copy()
    if not database_url:
        base_env.pop("DATABASE_URL", None)
    base_env.update(
        {
            "REDIS_URL": redis_url,
            "INVOICEOPS_DATA_DB": str(data_path),
            "INVOICEOPS_BATCH_DB": str(batch_path),
            "INVOICEOPS_CELERY_ENABLED": "true",
            "INVOICEOPS_TEST_PAUSE_AFTER_CHECKPOINT_SECONDS": "30",
        }
    )

    store = PersistentBatchStore(batch_path)
    job = store.create(content)
    first_hostname = f"invoiceops-recovery-first-{uuid4().hex[:8]}"
    second_hostname = f"invoiceops-recovery-second-{uuid4().hex[:8]}"
    log_dir = Path(os.getenv("INVOICEOPS_LIVE_WORKER_LOG_DIR", str(tmp_path / "worker-logs")))
    first_worker, first_log = _start_worker(base_env, first_hostname, log_dir / "first-worker.log")
    try:
        _wait_worker_ready(redis_url, first_worker, first_hostname)
        dispatcher = Celery("invoiceops-recovery-dispatcher", broker=redis_url)
        dispatcher.send_task("apps.worker.main.process_batch", args=[str(job.batch_id), "live-restart-dispatch"])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            processing = PersistentBatchStore(batch_path).get(job.batch_id)
            if processing and processing.status == BatchStatus.PROCESSING and processing.processed_rows >= 1:
                break
            assert first_worker.poll() is None, "the first worker exited before its checkpoint"
            time.sleep(0.25)
        else:
            raise AssertionError("the first worker did not reach a durable processing checkpoint")
    finally:
        _stop_worker(first_worker, first_log, force=True)

    recovery_env = base_env.copy()
    recovery_env.pop("INVOICEOPS_TEST_PAUSE_AFTER_CHECKPOINT_SECONDS", None)
    second_worker, second_log = _start_worker(recovery_env, second_hostname, log_dir / "second-worker.log")
    try:
        _wait_worker_ready(redis_url, second_worker, second_hostname)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            recovered = PersistentBatchStore(batch_path).get(job.batch_id)
            if recovered and recovered.status == BatchStatus.COMPLETED:
                break
            assert second_worker.poll() is None, "the recovering Celery worker exited"
            time.sleep(0.5)
        recovered = PersistentBatchStore(batch_path).get(job.batch_id)
        assert recovered is not None
        assert recovered.status == BatchStatus.COMPLETED
        assert recovered.success_rows == len(rows)
        assert recovered.failed_rows == 0
    finally:
        _stop_worker(second_worker, second_log)

    reloaded = PostgresRepository(database_url) if database_url else SQLiteRepository(data_path)
    tickets = [ticket for ticket in reloaded.tickets.values() if ticket.request_id in expected_request_ids]
    ticket_ids = {ticket.id for ticket in tickets}
    predictions = [prediction for prediction in reloaded.predictions.values() if prediction.ticket_id in ticket_ids]
    assert len(tickets) == len(rows)
    assert len(predictions) == len(rows)
    assert len({ticket.request_id for ticket in tickets}) == len(rows)
    if database_url:
        reloaded.close()
