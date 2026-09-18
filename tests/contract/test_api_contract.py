from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
OPENAPI_PATH = ROOT / "docs/api/openapi-v1.yaml"


def _load_openapi() -> dict:
    yaml = pytest.importorskip("yaml")
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def test_openapi_contract_has_required_endpoints_and_components() -> None:
    document = _load_openapi()

    assert document["openapi"] == "3.1.0"
    paths = document["paths"]
    assert {
        "/api/v1/classifications",
        "/api/v1/batches",
        "/api/v1/batches/{batch_id}",
        "/api/v1/reviews",
        "/api/v1/reviews/{ticket_id}/history",
        "/api/v1/reviews/{ticket_id}/history",
        "/api/v1/reviews/{ticket_id}/decisions",
        "/api/v1/admin/tickets",
        "/api/v1/admin/tickets/{ticket_id}",
        "/api/v1/admin/tickets/batch-delete",
        "/api/v1/admin/tickets/batch-overwrite",
        "/api/v1/admin/retention/cleanup",
        "/api/v1/taxonomies/current",
        "/api/v1/audit/{request_id}",
        "/healthz",
        "/readyz",
        "/metrics",
    } <= set(paths)
    assert "IdempotencyKey" in document["components"]["parameters"]
    assert "ErrorResponse" in document["components"]["schemas"]
    assert {"AdminTicket", "AdminBatchDeleteRequest", "AdminDeleteResponse", "AdminBatchOverwriteRequest", "AdminBatchOverwriteResponse"} <= set(document["components"]["schemas"])


def test_classification_example_matches_request_schema() -> None:
    document = _load_openapi()
    request = document["paths"]["/api/v1/classifications"]["post"]["requestBody"]["content"]["application/json"]["example"]

    assert set(request) == {"request_id", "source", "text", "taxonomy_version", "metadata"}
    assert request["taxonomy_version"] == "invoiceops-v1"
    assert request["text"]
