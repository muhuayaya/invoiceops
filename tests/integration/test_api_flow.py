from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from apps.api.main import create_app


def test_web_origin_preflight_is_allowed(monkeypatch) -> None:
    monkeypatch.setenv("INVOICEOPS_CORS_ORIGINS", "http://localhost:3000")
    client = TestClient(create_app())
    response = client.options(
        "/api/v1/auth/token",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_classification_idempotency_review_and_audit_flow() -> None:
    client = TestClient(create_app())
    payload = {
        "request_id": "integration-001",
        "source": "email",
        "text": "Please change the supplier bank account details.",
        "taxonomy_version": "invoiceops-v1",
    }
    first = client.post("/api/v1/classifications", headers={"Idempotency-Key": "integration-key"}, json=payload)
    assert first.status_code == 200
    assert first.json()["decision"] == "needs_review"
    assert "HIGH_RISK_LABEL" in first.json()["reason_codes"]
    repeated = client.post("/api/v1/classifications", headers={"Idempotency-Key": "integration-key"}, json=payload)
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    conflict = client.post("/api/v1/classifications", headers={"Idempotency-Key": "integration-key"}, json={**payload, "text": "different"})
    assert conflict.status_code == 409
    token = client.post("/api/v1/auth/token", json={"username": "reviewer", "password": "reviewer"}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    reviews = client.get("/api/v1/reviews", headers=auth)
    assert reviews.status_code == 200
    ticket_id = reviews.json()["items"][0]["ticket_id"]
    client.app.state.service.repository.get_ticket(UUID(ticket_id)).created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    overdue = client.get("/api/v1/reviews?waiting_min_seconds=3600", headers=auth)
    assert overdue.json()["items"][0]["overdue"] is True
    assert any(event["event_type"] == "review.sla_breached" for event in client.get("/api/v1/audit/integration-001", headers=auth).json()["events"])
    decision = client.post(f"/api/v1/reviews/{ticket_id}/decisions", headers={**auth, "X-Reviewer-Id": "forged-user"}, json={"labels": ["SUPPLIER_MASTER_CHANGE"], "primary_queue": "MASTER_DATA_RISK", "note": "verified"})
    assert decision.status_code == 201
    assert decision.json()["reviewer_id"] == "reviewer"
    admin = client.post("/api/v1/auth/token", json={"username": "admin", "password": "admin"}).json()["access_token"]
    second = client.post(f"/api/v1/reviews/{ticket_id}/decisions", headers={"Authorization": f"Bearer {admin}"}, json={"labels": ["SUPPLIER_MASTER_CHANGE"], "primary_queue": "MASTER_DATA_RISK", "note": "second review"})
    assert second.status_code == 201
    assert second.json()["revision"] == 2
    assert second.json()["reviewer_id"] == "admin"
    history = client.get(f"/api/v1/reviews/{ticket_id}/history", headers=auth)
    assert [item["revision"] for item in history.json()["items"]] == [1, 2]
    audit = client.get("/api/v1/audit/integration-001", headers=auth)
    assert audit.status_code == 200
    assert [event["event_type"] for event in audit.json()["events"]][-1] == "review.appended"


def test_role_matrix_health_metrics_and_retention() -> None:
    client = TestClient(create_app())
    observer = client.post("/api/v1/auth/token", json={"username": "observer", "password": "observer"}).json()["access_token"]
    admin = client.post("/api/v1/auth/token", json={"username": "admin", "password": "admin"}).json()["access_token"]
    assert client.get("/api/v1/reviews", headers={"Authorization": f"Bearer {observer}"}).status_code == 200
    denied = client.post("/api/v1/admin/retention/cleanup", headers={"Authorization": f"Bearer {observer}"})
    assert denied.status_code == 403
    assert any(event["event_type"] == "authorization.denied" for event in client.app.state.service.audit("security"))
    assert client.post("/api/v1/admin/retention/cleanup", headers={"Authorization": f"Bearer {admin}"}).status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}
    assert "invoiceops_tickets_total" in client.get("/metrics").text
    app = client.app
    app.state.model_ready = False
    assert client.get("/readyz").status_code == 503
