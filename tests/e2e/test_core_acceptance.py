from fastapi.testclient import TestClient

from apps.api.main import create_app
from invoiceops.ml_runtime.registry import ModelRegistry, ModelVersion


def test_core_acceptance_scenario() -> None:
    client = TestClient(create_app())
    payload = {
        "request_id": "e2e-multilabel-001",
        "source": "email",
        "text": "The invoice amount is wrong，而且我们还没有收到付款。",
        "taxonomy_version": "invoiceops-v1",
    }
    first = client.post("/api/v1/classifications", headers={"Idempotency-Key": "e2e-key"}, json=payload)
    assert first.status_code == 200
    assert first.json()["decision"] == "needs_review"
    assert {item["label_code"] for item in first.json()["predictions"]} >= {"TAX_CURRENCY_AMOUNT", "PAYMENT_STATUS"}
    conflict = client.post("/api/v1/classifications", headers={"Idempotency-Key": "e2e-key"}, json={**payload, "text": "different"})
    assert conflict.status_code == 409

    token = client.post("/api/v1/auth/token", json={"username": "reviewer", "password": "reviewer"}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/reviews", headers=auth).status_code == 200
    csv = "request_id,source,text,taxonomy_version\nvalid,email,Payment status?,invoiceops-v1\ninvalid,email,,invoiceops-v1\n"
    batch = client.post("/api/v1/batches", headers={**auth, "Idempotency-Key": "e2e-batch-key"}, files={"file": ("e2e.csv", csv.encode(), "text/csv")})
    assert batch.status_code == 202
    batch_status = client.get(f"/api/v1/batches/{batch.json()['batch_id']}", headers=auth).json()
    assert batch_status["success_rows"] == 1 and batch_status["failed_rows"] == 1

    registry = ModelRegistry()
    registry.register(ModelVersion("stable", "baseline", {"macro_f1": 0.81, "micro_f1": 0.86, "supplier_master_change_recall": 0.96}, "thresholds-v1"))
    registry.promote("stable")
    registry.register(ModelVersion("candidate", "xlmr", {"macro_f1": 0.82, "micro_f1": 0.87, "supplier_master_change_recall": 0.96}, "thresholds-v1"))
    registry.promote("candidate")
    assert registry.rollback().version == "stable"
