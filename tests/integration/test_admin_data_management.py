from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from apps.api.main import create_app
from invoiceops.adapters.sqlite import SQLiteRepository
from invoiceops.application.service import TriageService


def _token(client: TestClient, username: str) -> str:
    response = client.post("/api/v1/auth/token", json={"username": username, "password": username})
    assert response.status_code == 200
    return response.json()["access_token"]


def _headers(token: str, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def _payload(request_id: str, text: str = "The supplier bank account needs to be changed.") -> dict[str, object]:
    return {
        "request_id": request_id,
        "source": "manual",
        "text": text,
        "taxonomy_version": "invoiceops-v1",
        "metadata": {"locale": "en"},
    }


def test_admin_can_enter_filter_and_idempotently_overwrite_ticket() -> None:
    client = TestClient(create_app())
    admin = _token(client, "admin")
    auth = _headers(admin)

    created = client.post(
        "/api/v1/admin/tickets",
        headers=_headers(admin, "admin-enter-001"),
        json=_payload("admin-data-001"),
    )
    assert created.status_code == 200
    assert created.json()["request_id"] == "admin-data-001"

    listed = client.get("/api/v1/admin/tickets?request_id=admin-data-001", headers=auth)
    assert listed.status_code == 200
    before = listed.json()["items"]
    assert len(before) == 1
    assert "[ACCOUNT]" not in before[0]["sanitized_text"]
    old_ticket_id = before[0]["ticket_id"]

    overwrite_payload = {"items": [_payload("admin-data-001", "The invoice INV-2026-001 amount is wrong.")]}
    overwritten = client.post(
        "/api/v1/admin/tickets/batch-overwrite",
        headers=_headers(admin, "admin-overwrite-001"),
        json=overwrite_payload,
    )
    assert overwritten.status_code == 200
    assert overwritten.json()["overwritten_count"] == 1
    assert overwritten.json()["items"][0]["request_id"] == "admin-data-001"

    repeated = client.post(
        "/api/v1/admin/tickets/batch-overwrite",
        headers=_headers(admin, "admin-overwrite-001"),
        json=overwrite_payload,
    )
    assert repeated.status_code == 200
    assert repeated.json() == overwritten.json()

    current = client.get("/api/v1/admin/tickets?request_id=admin-data-001", headers=auth).json()["items"]
    assert len(current) == 1
    assert current[0]["ticket_id"] != old_ticket_id
    assert "[INVOICE]" in current[0]["sanitized_text"]
    audit = client.get("/api/v1/audit/admin-data-001", headers=auth)
    assert audit.status_code == 200
    assert "ticket.overwritten" in [event["event_type"] for event in audit.json()["events"]]

    conflict = client.post(
        "/api/v1/admin/tickets/batch-overwrite",
        headers=_headers(admin, "admin-overwrite-001"),
        json={"items": [_payload("admin-data-001", "another replacement")]},
    )
    assert conflict.status_code == 409

    single_deleted = client.delete(
        f"/api/v1/admin/tickets/{current[0]['ticket_id']}",
        headers=_headers(admin, "admin-single-delete-001"),
    )
    assert single_deleted.status_code == 200
    assert single_deleted.json()["deleted_count"] == 1
    assert client.get("/api/v1/admin/tickets?request_id=admin-data-001", headers=auth).json()["items"] == []


def test_admin_batch_mutations_prevalidate_and_delete_with_replay() -> None:
    client = TestClient(create_app())
    admin = _token(client, "admin")
    created_ids = []
    for index in range(2):
        response = client.post(
            "/api/v1/admin/tickets",
            headers=_headers(admin, f"admin-enter-batch-{index}"),
            json=_payload(f"admin-data-batch-{index}", "The invoice amount is wrong."),
        )
        assert response.status_code == 200
        created_ids.append(
            client.get(
                f"/api/v1/admin/tickets?request_id=admin-data-batch-{index}",
                headers=_headers(admin),
            ).json()["items"][0]["ticket_id"]
        )

    invalid = client.post(
        "/api/v1/admin/tickets/batch-delete",
        headers=_headers(admin, "admin-delete-invalid"),
        json={"ticket_ids": [created_ids[0], "00000000-0000-0000-0000-000000000000"]},
    )
    assert invalid.status_code == 404
    assert len(client.get("/api/v1/admin/tickets?request_id=admin-data-batch-0", headers=_headers(admin)).json()["items"]) == 1

    deleted_payload = {"ticket_ids": created_ids}
    deleted = client.post(
        "/api/v1/admin/tickets/batch-delete",
        headers=_headers(admin, "admin-delete-001"),
        json=deleted_payload,
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted_count"] == 2
    repeated = client.post(
        "/api/v1/admin/tickets/batch-delete",
        headers=_headers(admin, "admin-delete-001"),
        json=deleted_payload,
    )
    assert repeated.status_code == 200
    assert repeated.json() == deleted.json()
    assert client.get("/api/v1/admin/tickets?request_id=admin-data-batch-0", headers=_headers(admin)).json()["items"] == []


def test_admin_data_mutations_are_admin_only_and_invalid_overwrite_is_non_mutating() -> None:
    client = TestClient(create_app())
    admin = _token(client, "admin")
    reviewer = _token(client, "reviewer")
    observer = _token(client, "observer")
    created = client.post(
        "/api/v1/admin/tickets",
        headers=_headers(admin, "admin-enter-security"),
        json=_payload("admin-data-security"),
    )
    assert created.status_code == 200
    ticket_id = client.get(
        "/api/v1/admin/tickets?request_id=admin-data-security", headers=_headers(admin)
    ).json()["items"][0]["ticket_id"]

    assert client.get("/api/v1/admin/tickets", headers=_headers(reviewer)).status_code == 403
    assert client.delete(
        f"/api/v1/admin/tickets/{ticket_id}", headers=_headers(reviewer, "reviewer-delete")
    ).status_code == 403
    assert client.post(
        "/api/v1/admin/tickets/batch-overwrite",
        headers=_headers(observer, "observer-overwrite"),
        json={"items": [_payload("admin-data-security", "not allowed")]},
    ).status_code == 403
    anonymous = client.get("/api/v1/admin/tickets")
    assert anonymous.status_code == 401

    invalid = client.post(
        "/api/v1/admin/tickets/batch-overwrite",
        headers=_headers(admin, "admin-overwrite-invalid"),
        json={"items": [_payload("admin-data-security"), _payload("missing-request")]},
    )
    assert invalid.status_code == 404
    assert len(client.get("/api/v1/admin/tickets?request_id=admin-data-security", headers=_headers(admin)).json()["items"]) == 1
    assert any(
        event["event_type"] == "authorization.denied"
        for event in client.app.state.service.audit("security")
    )


def test_admin_delete_and_overwrite_are_persisted_in_sqlite(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "admin-data.sqlite3")
    service = TriageService(repository=repository)
    initial = _payload("sqlite-admin-001")
    service.classify(
        request_id=str(initial["request_id"]),
        source=str(initial["source"]),
        text=str(initial["text"]),
        taxonomy_version=str(initial["taxonomy_version"]),
        metadata={"locale": "en"},
        idempotency_key="sqlite-enter-001",
        trace_id="sqlite-trace-001",
    )
    original_id = repository.get_ticket_by_request("sqlite-admin-001").id

    service.overwrite_tickets(
        [_payload("sqlite-admin-001", "The INV-2026-002 amount is wrong.")],
        idempotency_key="sqlite-overwrite-001",
        actor="admin",
        trace_id="sqlite-trace-002",
    )
    reloaded = SQLiteRepository(tmp_path / "admin-data.sqlite3")
    replacement = reloaded.get_ticket_by_request("sqlite-admin-001")
    assert replacement is not None
    assert replacement.id != original_id
    assert replacement.text == "The [INVOICE] amount is wrong."

    service = TriageService(repository=reloaded)
    service.delete_tickets(
        [replacement.id], idempotency_key="sqlite-delete-001", actor="admin", trace_id="sqlite-trace-003"
    )
    assert SQLiteRepository(tmp_path / "admin-data.sqlite3").get_ticket_by_request("sqlite-admin-001") is None


def test_review_sla_and_revision_history_are_persisted_in_sqlite(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "review-history.sqlite3")
    service = TriageService(repository=repository, review_sla_minutes=1)
    result = service.classify(
        request_id="sqlite-review-001",
        source="manual",
        text="The supplier bank account needs to be changed.",
        taxonomy_version="invoiceops-v1",
        metadata={},
        idempotency_key="sqlite-review-enter-001",
        trace_id="sqlite-review-trace-001",
    )
    ticket = repository.get_ticket_by_request(result["request_id"])
    assert ticket is not None
    ticket.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    repository.persist_ticket(ticket)

    queue = service.list_reviews(None, None, 50)
    assert queue[0]["overdue"] is True
    assert any(event.event_type == "review.sla_breached" for event in repository.audit_for_request("sqlite-review-001"))
    service.review(ticket.id, ["SUPPLIER_MASTER_CHANGE"], "MASTER_DATA_RISK", "first", "reviewer", "review-trace-1")
    service.review(ticket.id, ["SUPPLIER_MASTER_CHANGE"], "MASTER_DATA_RISK", "second", "admin", "review-trace-2")

    reloaded = SQLiteRepository(tmp_path / "review-history.sqlite3")
    history = TriageService(repository=reloaded).review_history(ticket.id)
    assert [item["revision"] for item in history] == [1, 2]
    assert [item["reviewer_id"] for item in history] == ["reviewer", "admin"]
