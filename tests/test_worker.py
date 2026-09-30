from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from apps.api.main import create_app
from apps.worker import tasks
from invoiceops.adapters import db
from invoiceops.data.contract import LABELS


class PrimaryModel:
    labels = LABELS
    thresholds = (0.5,) * 8
    version = "primary-test"
    threshold_version = "primary-thresholds"
    engineering_approved = True

    def predict_proba(self, texts):
        if "fallback" in texts[0]:
            raise RuntimeError("primary inference failed")
        return [[0.9, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]]


class FallbackModel(PrimaryModel):
    version = "fallback-test"
    threshold_version = "fallback-thresholds"

    def predict_proba(self, texts):
        return [[0.1, 0.1, 0.1, 0.8, 0.1, 0.1, 0.1, 0.1]]


def test_batch_restart_is_idempotent_and_records_mixed_versions(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(tasks, "_database_engine", lambda: engine)
    monkeypatch.setattr(tasks, "_runtime", lambda path, role: PrimaryModel() if role == "primary" else FallbackModel())
    monkeypatch.setattr(tasks.process_batch, "delay", lambda batch_id: None)
    monkeypatch.setenv("INVOICEOPS_PRIMARY_MODEL", "primary")
    monkeypatch.setenv("INVOICEOPS_FALLBACK_MODEL", "fallback")
    with TestClient(create_app(engine=engine, primary=PrimaryModel())) as api:
        email, password = "batch@example.test", "TestPassword123"
        api.post("/v1/auth/register", json={"email": email, "password": password})
        token = api.post("/v1/auth/login", json={"email": email, "password": password}).json()["token"]
        headers = {"Authorization": "Bearer " + token}
        csv_data = b"text,external_id\nDuplicate invoice,1\nUse fallback for payment,2\n,3\n"
        response = api.post("/v1/batches", files={"file": ("mixed.csv", csv_data, "text/csv")}, headers=headers)
        assert response.status_code == 202, response.text
        batch_id = response.json()["id"]
        tasks.process_batch.run(batch_id)
        status = api.get(f"/v1/batches/{batch_id}", headers=headers).json()
        assert (status["succeeded"], status["failed"], status["pending"]) == (2, 1, 0)
        result_csv = api.get(f"/v1/batches/{batch_id}/download", headers=headers).text
        assert "primary-test" in result_csv and "fallback-test" in result_csv
        assert "empty_text" in result_csv
        with db.new_session(engine) as session:
            before = session.scalar(select(func.count()).select_from(db.Ticket))
        tasks.process_batch.run(batch_id)
        with db.new_session(engine) as session:
            after = session.scalar(select(func.count()).select_from(db.Ticket))
        assert before == after == 2
