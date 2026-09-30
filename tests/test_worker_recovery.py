from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from apps.worker import tasks
from invoiceops.adapters import db
from invoiceops.data.contract import LABELS


class ApprovedModel:
    labels = LABELS
    thresholds = (0.5,) * 8
    version = "primary-recovered"
    threshold_version = "thresholds-recovered"
    engineering_approved = True

    def predict_proba(self, texts):
        return [[0.9, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]]


def test_unavailable_model_pauses_and_recovers_without_duplicate_ticket(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    with db.new_session(engine) as session, session.begin():
        batch = db.BatchJob(submitted_by="submitter", total_rows=1)
        session.add(batch)
        session.flush()
        session.add(db.BatchItem(batch_id=batch.id, row_number=2, redacted_text="Duplicate invoice"))
        batch_id = batch.id
    monkeypatch.setattr(tasks, "_database_engine", lambda: engine)
    monkeypatch.setenv("INVOICEOPS_PRIMARY_MODEL", "primary")
    monkeypatch.setattr(tasks, "_runtime", lambda path, role: None)
    tasks.process_batch.run(batch_id)
    with db.new_session(engine) as session:
        batch = session.get(db.BatchJob, batch_id)
        item = session.scalar(select(db.BatchItem).where(db.BatchItem.batch_id == batch_id))
        assert batch.status == "paused"
        assert item.status == "pending" and item.error_code == "model_unavailable"
    monkeypatch.setattr(tasks, "_runtime", lambda path, role: ApprovedModel() if role == "primary" else None)
    tasks.process_batch.run(batch_id)
    tasks.process_batch.run(batch_id)
    with db.new_session(engine) as session:
        batch = session.get(db.BatchJob, batch_id)
        item = session.scalar(select(db.BatchItem).where(db.BatchItem.batch_id == batch_id))
        tickets = list(session.scalars(select(db.Ticket)))
        assert batch.status == "completed"
        assert item.status == "succeeded" and item.error_code is None
        assert len(tickets) == 1
