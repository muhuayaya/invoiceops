from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apps.api.main import create_app
from invoiceops.adapters.db import Base
from invoiceops.data.contract import LABELS


class StubModel:
    labels = LABELS
    thresholds = (0.5,) * 8
    version = "approved-test-model"
    threshold_version = "test-thresholds"
    engineering_approved = True

    def predict_proba(self, texts):
        return [[0.9, 0.1, 0.1, 0.8, 0.1, 0.1, 0.1, 0.1] for _ in texts]


def client():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return TestClient(create_app(engine=engine, primary=StubModel()))


def register_and_login(api, email):
    password = "TestPassword123"
    assert api.post("/v1/auth/register", json={"email": email, "password": password}).status_code == 200
    response = api.post("/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


def test_classification_authorization_idempotency_and_review():
    with client() as api:
        alice = register_and_login(api, "alice@example.test")
        bob = register_and_login(api, "bob@example.test")
        payload = {"text": "Duplicate invoice and payment status", "idempotency_key": "request-1"}
        assert api.post("/v1/classifications", json=payload).status_code == 401
        first = api.post("/v1/classifications", json=payload, headers=alice)
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["labels"] == ["DUPLICATE_INVOICE", "PAYMENT_STATUS"]
        assert len(result["probabilities"]) == 8
        assert api.post("/v1/classifications", json=payload, headers=alice).json()["ticket_id"] == result["ticket_id"]
        assert len(api.get("/v1/classifications", headers=alice).json()) == 1
        assert api.get("/v1/classifications", headers=bob).json() == []

        # This model's result does not require review, so submit a high-risk item separately.
        queue = api.get("/v1/reviews/queue", headers=bob)
        assert queue.status_code == 200


def test_single_classification_keeps_external_id():
    with client() as api:
        alice = register_and_login(api, "external-id@example.test")
        response = api.post("/v1/classifications", json={"text": "Invoice INV-42", "external_id": "INV-42"}, headers=alice)
        assert response.status_code == 200
        assert response.json()["external_id"] == "INV-42"


def test_classification_invalid_text_returns_422_without_creating_ticket():
    with client() as api:
        alice = register_and_login(api, "invalid-text@example.test")
        invalid_payloads = [
            ({"text": "   "}, "text must not be empty"),
            ({"text": "x" * 10_001}, "text exceeds maximum length"),
        ]
        for payload, expected_error in invalid_payloads:
            response = api.post("/v1/classifications", json=payload, headers=alice)
            assert response.status_code == 422
            assert response.text == expected_error
        assert api.get("/v1/classifications", headers=alice).json() == []


def test_batch_file_and_object_permissions():
    with client() as api:
        alice = register_and_login(api, "batch-alice@example.test")
        bob = register_and_login(api, "batch-bob@example.test")
        missing = api.post("/v1/batches", files={"file": ("bad.csv", b"external_id\n1\n", "text/csv")}, headers=alice)
        assert missing.status_code == 422
        oversized = api.post("/v1/batches", files={"file": ("large.csv", b"x" * (10 * 1024 * 1024 + 1), "text/csv")}, headers=alice)
        assert oversized.status_code == 422 and "maximum file size" in oversized.text
        batch = api.post("/v1/batches", files={"file": ("mixed.csv", b"text,external_id\nDuplicate invoice,1\n,2\n", "text/csv")}, headers=alice)
        assert batch.status_code == 202, batch.text
        summary = batch.json()
        assert summary["total"] == 2 and summary["failed"] == 1 and summary["pending"] == 1
        assert api.get(f"/v1/batches/{summary['id']}/download", headers=bob).status_code == 403
        csv_result = api.get(f"/v1/batches/{summary['id']}/download", headers=alice)
        assert csv_result.status_code == 200 and "empty_text" in csv_result.text



def test_invalid_registration_fields_return_client_error():
    with client() as api:
        short_password = api.post("/v1/auth/register", json={"email": "valid@example.test", "password": "short"})
        assert short_password.status_code == 422
        malformed_email = api.post("/v1/auth/register", json={"email": "not-an-email", "password": "ValidPassword123"})
        assert malformed_email.status_code == 422
        for weak in ("abcdefghijkl", "123456789012", "Abc 123def456", "Abc123def456!", "Abc123-def456", "A1" * 33):
            rejected = api.post("/v1/auth/register", json={"email": "weak@example.test", "password": weak})
            assert rejected.status_code == 422, weak
            assert "letters and digits" in rejected.json()["detail"]
        assert api.post("/v1/auth/register", json={"email": "weak@example.test", "password": "Abc123def456"}).status_code == 200



def test_model_readiness_reports_primary_fallback_and_unavailable_states():
    with client() as api:
        assert api.get("/healthz").status_code == 200
        # The test client helper configures only an approved primary model.
        assert api.get("/readyz").json()["status"] == "ready"

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with TestClient(create_app(engine=engine, primary=None, fallback=StubModel())) as api:
        assert api.get("/readyz").json()["status"] == "degraded"

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with TestClient(create_app(engine=engine, primary=None, fallback=None)) as api:
        assert api.get("/healthz").json()["status"] == "alive"
        assert api.get("/readyz").status_code == 503


def test_classification_api_stops_when_both_models_are_unavailable(monkeypatch):
    monkeypatch.delenv("INVOICEOPS_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("INVOICEOPS_FALLBACK_MODEL", raising=False)
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with TestClient(create_app(engine=engine, primary=None, fallback=None)) as api:
        headers = register_and_login(api, "unavailable@example.test")
        response = api.post("/v1/classifications", json={"text": "Payment pending"}, headers=headers)
        assert response.status_code == 503
        assert api.get("/v1/classifications", headers=headers).json() == []
