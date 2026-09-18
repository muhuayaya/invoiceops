from fastapi.testclient import TestClient

from apps.api.main import create_app
from invoiceops.application.service import TriageService
from invoiceops.ml_runtime.rules import KeywordClassifier


def test_batch_requires_reviewer_or_admin_authentication() -> None:
    client = TestClient(create_app())
    content = b"request_id,source,text,taxonomy_version\nsecurity-1,email,Payment status?,invoiceops-v1\n"
    response = client.post(
        "/api/v1/batches",
        headers={"Idempotency-Key": "security-batch-001"},
        files={"file": ("batch.csv", content, "text/csv")},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_validation_error_does_not_echo_sensitive_input() -> None:
    client = TestClient(create_app())
    sensitive = "secret@example.com"
    response = client.post(
        "/api/v1/classifications",
        headers={"Idempotency-Key": "security-validation-001"},
        json={"request_id": "security-validation-001", "text": sensitive, "taxonomy_version": "invoiceops-v1"},
    )
    assert response.status_code == 422
    assert sensitive not in response.text
    assert "input" not in response.text


def test_batch_size_limit_is_enforced(monkeypatch) -> None:
    monkeypatch.setenv("INVOICEOPS_MAX_BATCH_BYTES", "32")
    client = TestClient(create_app())
    token = client.post("/api/v1/auth/token", json={"username": "reviewer", "password": "reviewer"}).json()["access_token"]
    response = client.post(
        "/api/v1/batches",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "security-size-001"},
        files={"file": ("batch.csv", b"x" * 33, "text/csv")},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "DOMAIN_VALIDATION_ERROR"


def test_unhandled_classifier_error_returns_traceable_contract() -> None:
    class ExplodingClassifier(KeywordClassifier):
        def predict(self, text: str, *, sanitized: bool = True):
            raise RuntimeError("internal test failure")

    client = TestClient(create_app(TriageService(classifier=ExplodingClassifier())))
    response = client.post(
        "/api/v1/classifications",
        headers={"Idempotency-Key": "security-error-001"},
        json={
            "request_id": "security-error-001",
            "source": "email",
            "text": "Payment is pending.",
            "taxonomy_version": "invoiceops-v1",
        },
    )
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert response.headers["X-Trace-Id"] == response.json()["trace_id"]
