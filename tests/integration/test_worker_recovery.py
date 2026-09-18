import pytest

from invoiceops.adapters.sqlite import SQLiteRepository
from invoiceops.batch import BatchProcessor, BatchStatus
from invoiceops.batch_store import PersistentBatchStore
from invoiceops.application.service import TriageService


def test_celery_eager_worker_processes_durable_job(monkeypatch, tmp_path) -> None:
    worker = pytest.importorskip("apps.worker.main")
    if worker.celery_app is None:
        pytest.skip("Celery is not installed")
    path = tmp_path / "worker.sqlite3"
    data_path = tmp_path / "business.sqlite3"
    monkeypatch.setenv("INVOICEOPS_BATCH_DB", str(path))
    monkeypatch.setenv("INVOICEOPS_DATA_DB", str(data_path))
    payload = b"request_id,source,text,taxonomy_version\nworker-1,email,Payment status?,invoiceops-v1\n"
    job = BatchProcessor(TriageService(), PersistentBatchStore(path)).create(payload)
    worker.celery_app.conf.task_always_eager = True
    result = worker.process_batch.apply(args=[str(job.batch_id), "trace-worker"])
    assert result.get() == {"batch_id": str(job.batch_id), "trace_id": "trace-worker"}
    assert PersistentBatchStore(path).get(job.batch_id).success_rows == 1
    assert len(SQLiteRepository(data_path).tickets) == 1


def test_worker_ready_requeues_incomplete_jobs(monkeypatch, tmp_path) -> None:
    worker = pytest.importorskip("apps.worker.main")
    if worker.celery_app is None:
        pytest.skip("Celery is not installed")
    path = tmp_path / "recovery.sqlite3"
    monkeypatch.setenv("INVOICEOPS_BATCH_DB", str(path))
    store = PersistentBatchStore(path)
    job = store.create(b"request_id,source,text,taxonomy_version\nrequeue-1,email,Payment status?,invoiceops-v1\n")
    job.status = BatchStatus.PROCESSING
    store.save(job)
    submitted: list[tuple[str, str]] = []
    monkeypatch.setattr(worker.process_batch, "delay", lambda batch_id, trace_id: submitted.append((batch_id, trace_id)))

    worker.recover_incomplete_batches()

    assert submitted and submitted[0][0] == str(job.batch_id)
    assert PersistentBatchStore(path).get(job.batch_id).status.value == "queued"


def test_worker_ready_does_not_requeue_queued_jobs(monkeypatch, tmp_path) -> None:
    worker = pytest.importorskip("apps.worker.main")
    if worker.celery_app is None:
        pytest.skip("Celery is not installed")
    path = tmp_path / "queued.sqlite3"
    monkeypatch.setenv("INVOICEOPS_BATCH_DB", str(path))
    job = BatchProcessor(TriageService(), PersistentBatchStore(path)).create(
        b"request_id,source,text,taxonomy_version\nqueued,email,Payment status?,invoiceops-v1\n"
    )
    submitted = []
    monkeypatch.setattr(worker.process_batch, "delay", lambda *args: submitted.append(args))

    worker.recover_incomplete_batches()

    assert submitted == []
    assert PersistentBatchStore(path).get(job.batch_id).status.value == "queued"
