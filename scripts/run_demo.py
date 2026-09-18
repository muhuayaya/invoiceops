"""Run the local M0-M4 demonstration path and save machine-readable evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from apps.api.main import create_app  # noqa: E402


def main() -> None:
    client = TestClient(create_app())
    payload = {
        "request_id": "demo-evidence-001",
        "source": "email",
        "text": "The invoice amount is wrong，而且我们还没有收到付款。",
        "taxonomy_version": "invoiceops-v1",
    }
    classification = client.post("/api/v1/classifications", headers={"Idempotency-Key": "demo-evidence-key"}, json=payload)
    classification_result = classification.json()
    token = client.post("/api/v1/auth/token", json={"username": "reviewer", "password": "reviewer"}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    reviews = client.get("/api/v1/reviews", headers=auth)
    ticket_id = reviews.json()["items"][0]["ticket_id"]
    review = client.post(f"/api/v1/reviews/{ticket_id}/decisions", headers=auth, json={"labels": ["PAYMENT_STATUS"], "primary_queue": "FINANCE_TAX", "note": "demo"})
    audit = client.get("/api/v1/audit/demo-evidence-001", headers=auth)
    batch_csv = b"request_id,source,text,taxonomy_version\ndemo-batch-1,email,Payment status?,invoiceops-v1\ndemo-batch-2,email,,invoiceops-v1\n"
    batch = client.post("/api/v1/batches", headers={**auth, "Idempotency-Key": "demo-batch-key"}, files={"file": ("demo.csv", batch_csv, "text/csv")})
    batch_status = client.get(f"/api/v1/batches/{batch.json()['batch_id']}", headers=auth)
    metrics = client.get("/metrics")
    evidence = {
        "evidence_version": "demo-v2",
        "classification_status": classification.status_code,
        "classification_decision": classification_result.get("decision"),
        "classification_predictions": classification_result.get("predictions", []),
        "classification_reason_codes": classification_result.get("reason_codes", []),
        "classification_route": classification_result.get("route"),
        "classification_versions": {
            "model": classification_result.get("model_version"),
            "threshold": classification_result.get("threshold_version"),
            "taxonomy": classification_result.get("taxonomy_version"),
        },
        "review_status": review.status_code,
        "audit_status": audit.status_code,
        "audit_event_count": len(audit.json().get("events", [])),
        "batch_status": batch_status.status_code,
        "batch_result": batch_status.json(),
        "metrics_status": metrics.status_code,
        "metrics_excerpt": metrics.text[:2000],
        "passed": classification.status_code == 200 and review.status_code == 201 and audit.status_code == 200 and batch_status.json().get("failed_rows") == 1 and metrics.status_code == 200,
    }
    path = Path("docs/delivery/demo-evidence-v2.json")
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
