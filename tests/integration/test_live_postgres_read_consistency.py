from fastapi.testclient import TestClient

from apps.api.main import create_app
from invoiceops.adapters.postgres import PostgresRepository
from invoiceops.application.service import TriageService


def _token(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/v1/auth/token",
        json={"username": username, "password": username},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_api_reads_worker_commits_without_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'shared.sqlite3'}"
    api_repository = PostgresRepository(database_url)
    client = TestClient(create_app(TriageService(repository=api_repository)))

    worker_repository = PostgresRepository(database_url)
    TriageService(repository=worker_repository).classify(
        request_id="worker-committed-001",
        source="email",
        text="Please change the supplier bank account details.",
        taxonomy_version="invoiceops-v1",
        metadata={"channel": "email"},
        idempotency_key="worker-committed-key",
        trace_id="worker-committed-trace",
    )

    admin = {"Authorization": f"Bearer {_token(client, 'admin')}"}
    reviewer = {"Authorization": f"Bearer {_token(client, 'reviewer')}"}

    tickets = client.get("/api/v1/admin/tickets", headers=admin)
    assert tickets.status_code == 200
    assert [item["request_id"] for item in tickets.json()["items"]] == ["worker-committed-001"]
    ticket_id = tickets.json()["items"][0]["ticket_id"]

    reviews = client.get("/api/v1/reviews", headers=reviewer)
    assert reviews.status_code == 200
    assert [item["request_id"] for item in reviews.json()["items"]] == ["worker-committed-001"]

    history = client.get(f"/api/v1/reviews/{ticket_id}/history", headers=reviewer)
    assert history.status_code == 200
    assert history.json()["items"] == []

    audit = client.get("/api/v1/audit/worker-committed-001", headers=reviewer)
    assert audit.status_code == 200
    assert len(audit.json()["events"]) == 3

    metrics = client.get("/metrics")
    assert "invoiceops_tickets_total 1" in metrics.text
    assert "invoiceops_audit_events_total 3" in metrics.text

    repeated = client.get("/api/v1/admin/tickets", headers=admin)
    assert len(repeated.json()["items"]) == 1
    repeated_audit = client.get("/api/v1/audit/worker-committed-001", headers=reviewer)
    assert len(repeated_audit.json()["events"]) == 3
    repeated_metrics = client.get("/metrics")
    assert "invoiceops_audit_events_total 3" in repeated_metrics.text


def test_reviewed_status_is_visible_to_admin_without_api_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'review-status.sqlite3'}"
    repository = PostgresRepository(database_url)
    client = TestClient(create_app(TriageService(repository=repository)))
    admin = {"Authorization": f"Bearer {_token(client, 'admin')}"}
    reviewer = {"Authorization": f"Bearer {_token(client, 'reviewer')}"}

    created = client.post(
        "/api/v1/admin/tickets",
        headers={**admin, "Idempotency-Key": "review-status-enter-001"},
        json={
            "request_id": "review-status-001",
            "source": "manual",
            "text": "Please change the supplier bank account details.",
            "taxonomy_version": "invoiceops-v1",
            "metadata": {"locale": "en"},
        },
    )
    assert created.status_code == 200

    pending = client.get("/api/v1/reviews", headers=reviewer)
    assert pending.status_code == 200
    item = next(item for item in pending.json()["items"] if item["request_id"] == "review-status-001")
    decision = client.post(
        f"/api/v1/reviews/{item['ticket_id']}/decisions",
        headers=reviewer,
        json={
            "labels": ["OTHER_REVIEW"],
            "primary_queue": "MANUAL_TRIAGE",
            "note": "确认供应商主数据变更工单。",
        },
    )
    assert decision.status_code == 201

    admin_items = client.get("/api/v1/admin/tickets", headers=admin).json()["items"]
    reviewed = next(item for item in admin_items if item["request_id"] == "review-status-001")
    assert reviewed["status"] == "reviewed"
    assert reviewed["labels"] == ["OTHER_REVIEW"]
    assert reviewed["primary_queue"] == "MANUAL_TRIAGE"
    assert client.get("/api/v1/reviews", headers=reviewer).json()["items"] == []
