from fastapi.testclient import TestClient

from apps.api.main import create_app


def test_batch_processes_valid_rows_and_reports_invalid_rows() -> None:
    client = TestClient(create_app())
    token = client.post("/api/v1/auth/token", json={"username": "reviewer", "password": "reviewer"}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    content = "request_id,source,text,taxonomy_version,channel\nvalid-1,email,Payment status?,invoiceops-v1,email\ninvalid-1,email,,invoiceops-v1,email\n"
    response = client.post("/api/v1/batches", headers={**auth, "Idempotency-Key": "batch-flow-001"}, files={"file": ("batch.csv", content.encode(), "text/csv")})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    fetched = client.get(f"/api/v1/batches/{body['batch_id']}", headers=auth)
    assert fetched.json()["success_rows"] == 1
    assert fetched.json()["failed_rows"] == 1
    assert fetched.json()["row_errors"][0]["row_number"] == 3
    assert fetched.json()["status"] == "completed"


def test_batch_accepts_utf8_bom_csv() -> None:
    client = TestClient(create_app())
    token = client.post("/api/v1/auth/token", json={"username": "reviewer", "password": "reviewer"}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    content = "\ufeffrequest_id,source,text,taxonomy_version\nbom-1,email,Payment status?,invoiceops-v1\n"

    response = client.post("/api/v1/batches", headers={**auth, "Idempotency-Key": "batch-flow-bom-001"}, files={"file": ("batch.csv", content.encode("utf-8"), "text/csv")})

    assert response.status_code == 202
    fetched = client.get(f"/api/v1/batches/{response.json()['batch_id']}", headers=auth)
    assert fetched.json()["status"] == "completed"
    assert fetched.json()["success_rows"] == 1
