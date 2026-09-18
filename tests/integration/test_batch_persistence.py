from invoiceops.application.service import TriageService
from invoiceops.batch import BatchProcessor, BatchStatus
from invoiceops.batch_store import PersistentBatchStore


def _csv() -> bytes:
    return b"request_id,source,text,taxonomy_version\nrestart-1,email,Payment status?,invoiceops-v1\n"


def test_batch_checkpoint_recovers_after_processor_restart(tmp_path) -> None:
    path = tmp_path / "batches.sqlite3"
    first_store = PersistentBatchStore(path)
    first = BatchProcessor(TriageService(), first_store)
    job = first.create(_csv())
    job.status = BatchStatus.PROCESSING
    first_store.save(job)
    assert first_store.recover_incomplete() == [job.batch_id]

    second_store = PersistentBatchStore(path)
    second = BatchProcessor(TriageService(), second_store)
    recovered = second.process(job.batch_id)
    assert recovered.status.value == "completed"
    assert recovered.success_rows == 1

    replayed = second.process(job.batch_id)
    assert replayed.success_rows == 1
    assert replayed.processed_rows == 1
