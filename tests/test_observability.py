import logging

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apps.api.main import create_app
from invoiceops.adapters import db
from invoiceops.adapters.db import Base
from invoiceops.data.contract import LABELS
from invoiceops.observability.monitoring import RequestLimiter
from invoiceops.security.auth import bootstrap_admin


class Model:
    labels = LABELS
    thresholds = (0.5,) * 8
    version = "metric-model"
    threshold_version = "metric-thresholds"
    engineering_approved = True

    def predict_proba(self, texts):
        return [[0.9] + [0.1] * 7]


def test_request_id_metrics_and_logs_omit_ticket_text(caplog):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with db.new_session(engine) as session, session.begin():
        bootstrap_admin(session, "admin@example.test", "AdminPassword123")
    with TestClient(create_app(engine=engine, primary=Model())) as api:
        password = "TestPassword123"
        api.post("/v1/auth/register", json={"email": "metrics@example.test", "password": password})
        token = api.post("/v1/auth/login", json={"email": "metrics@example.test", "password": password}).json()["token"]
        headers = {"Authorization": "Bearer " + token}
        secret_text = "Invoice 42, email secret@example.test"
        with caplog.at_level(logging.INFO, logger="invoiceops.requests"):
            response = api.post("/v1/classifications", json={"text": secret_text}, headers=headers)
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID")
        assert api.get("/metrics").status_code == 401
        assert api.get("/metrics", headers=headers).status_code == 403
        admin_token = api.post("/v1/auth/login", json={"email": "admin@example.test", "password": "AdminPassword123"}).json()["token"]
        metrics = api.get("/metrics", headers={"Authorization": "Bearer " + admin_token}).json()["classifications"]
        assert any(row["model_version"] == "metric-model" and row["requests"] == 1 for row in metrics)
        assert secret_text not in caplog.text and token not in caplog.text
        assert "[EMAIL]" not in caplog.text



def test_request_limiter_reclaims_expired_keys_and_caps_memory(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("invoiceops.observability.monitoring.time.monotonic", lambda: now[0])
    limiter = RequestLimiter(window_seconds=10, max_requests=1, max_keys=2)
    assert limiter.allow("a")
    assert limiter.allow("b")
    assert not limiter.allow("c")
    now[0] = 11.0
    for _ in range(253):
        limiter.allow("a")
    assert len(limiter._requests) <= 2
    assert limiter.allow("c")
    assert len(limiter._requests) <= 2
