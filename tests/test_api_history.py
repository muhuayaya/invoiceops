from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apps.api.main import create_app
from invoiceops.adapters import db
from invoiceops.data.contract import LABELS
from invoiceops.security.auth import bootstrap_admin, register_user


class RiskModel:
    labels = LABELS
    thresholds = (0.5,) * 8
    version = "approved-test-risk-model"
    threshold_version = "test-thresholds"
    engineering_approved = True

    def predict_proba(self, texts):
        return [[0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.95, 0.1] for _ in texts]


def test_self_review_allowed_and_history_survives_admin_delete():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    with db.new_session(engine) as session, session.begin():
        bootstrap_admin(session, "admin@example.test", "AdminPassword123")
    with TestClient(create_app(engine=engine, primary=RiskModel())) as api:
        password = "UserPassword123"
        for email in ("alice@example.test", "bob@example.test"):
            assert api.post("/v1/auth/register", json={"email": email, "password": password}).status_code == 200

        def headers(email, secret):
            response = api.post("/v1/auth/login", json={"email": email, "password": secret})
            assert response.status_code == 200
            return {"Authorization": "Bearer " + response.json()["token"]}

        alice = headers("alice@example.test", password)
        bob = headers("bob@example.test", password)
        administrator = headers("admin@example.test", "AdminPassword123")
        result = api.post("/v1/classifications", json={"text": "Please change the supplier bank account"}, headers=alice)
        assert result.status_code == 200, result.text
        record = result.json()
        assert record["review_status"] == "pending_review"
        review = {"expected_version": record["state_version"], "decision": "corrected", "final_labels": ["OTHER_REVIEW"], "comment": "Verified"}
        assert api.post(f"/v1/reviews/{record['ticket_id']}", json=review, headers=alice).status_code == 200
        assert api.post(f"/v1/reviews/{record['ticket_id']}", json=review, headers=bob).status_code == 409
        reviewed = api.get("/v1/classifications", headers=alice).json()[0]
        assert reviewed["labels"] == ["SUPPLIER_MASTER_CHANGE"]
        assert reviewed["final_labels"] == ["OTHER_REVIEW"]
        assert api.delete(f"/v1/admin/tickets/{record['ticket_id']}", headers=bob).status_code == 403
        assert api.delete(f"/v1/admin/tickets/{record['ticket_id']}", headers=administrator).status_code == 200
        history = api.get("/v1/admin/history", headers=administrator)
        assert history.status_code == 200
        payload = history.json()
        assert any(row["ticket_id"] == record["ticket_id"] for row in payload["prediction_history"])
        assert any(row["ticket_id"] == record["ticket_id"] for row in payload["review_decisions"])
        assert any(row["action"] == "ticket.delete" and row["object_id"] == record["ticket_id"] for row in payload["audit_events"])



def test_deleted_batch_rows_remain_in_admin_history():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    with db.new_session(engine) as session, session.begin():
        bootstrap_admin(session, "admin@example.test", "AdminPassword123")

    with TestClient(create_app(engine=engine, primary=RiskModel())) as api:
        assert api.post("/v1/auth/register", json={"email": "submitter@example.test", "password": "UserPassword123"}).status_code == 200
        admin_login = api.post("/v1/auth/login", json={"email": "admin@example.test", "password": "AdminPassword123"})
        user_login = api.post("/v1/auth/login", json={"email": "submitter@example.test", "password": "UserPassword123"})
        admin = {"Authorization": "Bearer " + admin_login.json()["token"]}
        user = {"Authorization": "Bearer " + user_login.json()["token"]}
        batch = api.post("/v1/batches", files={"file": ("rows.csv", b"text,external_id\nChange supplier,ext-1\n,ext-2\n", "text/csv")}, headers=user)
        assert batch.status_code == 202, batch.text
        batch_id = batch.json()["id"]
        assert api.delete(f"/v1/admin/batches/{batch_id}", headers=admin).status_code == 200

        history = api.get("/v1/admin/history", headers=admin)
        assert history.status_code == 200
        payload = history.json()
        archived = [row for row in payload["batch_item_history"] if row["batch_id"] == batch_id]
        assert len(archived) == 2
        assert {row["external_id"] for row in archived} == {"ext-1", "ext-2"}
        assert any(row["action"] == "batch.delete" and row["object_id"] == batch_id for row in payload["audit_events"])


def test_admin_bulk_delete_is_atomic_authorized_and_audited():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    with db.new_session(engine) as session, session.begin():
        bootstrap_admin(session, "admin@example.test", "AdminPassword123")

    with TestClient(create_app(engine=engine, primary=RiskModel())) as api:
        api.post("/v1/auth/register", json={"email": "submitter@example.test", "password": "UserPassword123"})
        admin_login = api.post("/v1/auth/login", json={"email": "admin@example.test", "password": "AdminPassword123"})
        user_login = api.post("/v1/auth/login", json={"email": "submitter@example.test", "password": "UserPassword123"})
        admin = {"Authorization": "Bearer " + admin_login.json()["token"]}
        user = {"Authorization": "Bearer " + user_login.json()["token"]}

        records = [api.post("/v1/classifications", json={"text": f"Change the supplier bank details {index}"}, headers=user).json() for index in (1, 2)]
        prediction_ids = [row["prediction_id"] for row in records]
        ticket_ids = [row["ticket_id"] for row in records]
        batch = api.post("/v1/batches", files={"file": ("rows.csv", b"text,external_id\nChange supplier,ext-1\n", "text/csv")}, headers=user)
        assert batch.status_code == 202
        batch_id = batch.json()["id"]

        assert api.request("DELETE", "/v1/admin/tickets", json={"ids": ticket_ids}, headers=user).status_code == 403
        partial = api.request("DELETE", "/v1/admin/tickets", json={"ids": [ticket_ids[0], "missing"]}, headers=admin)
        assert partial.status_code == 404
        assert {row["ticket_id"] for row in api.get("/v1/admin/tickets", headers=admin).json()} >= set(ticket_ids)

        assert api.request("DELETE", "/v1/admin/predictions", json={"ids": prediction_ids}, headers=admin).json()["count"] == 2
        assert api.request("DELETE", "/v1/admin/tickets", json={"ids": ticket_ids}, headers=admin).json()["count"] == 2
        assert api.request("DELETE", "/v1/admin/batches", json={"ids": [batch_id]}, headers=admin).json()["count"] == 1

        history = api.get("/v1/admin/history", headers=admin).json()
        for object_id in ticket_ids:
            assert any(row["action"] == "ticket.delete" and row["object_id"] == object_id for row in history["audit_events"])
        for object_id in prediction_ids:
            assert any(row["action"] == "prediction.delete" and row["object_id"] == object_id for row in history["audit_events"])
        assert any(row["action"] == "batch.delete" and row["object_id"] == batch_id for row in history["audit_events"])


def test_batch_download_includes_review_reasons():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    with db.new_session(engine) as session, session.begin():
        user = register_user(session, "submitter@example.test", "UserPassword123")
        ticket = db.create_ticket(session, user.id, "redacted text")
        db.add_prediction(session, ticket.id, probabilities={"SUPPLIER_MASTER_CHANGE": 0.95}, labels=["SUPPLIER_MASTER_CHANGE"], model_version="test-model", threshold_version="test-threshold", taxonomy_version="invoiceops-v1", review_reason="supplier_master_change|low_confidence")
        batch = db.BatchJob(submitted_by=user.id, total_rows=1, status="completed")
        session.add(batch)
        session.flush()
        session.add(db.BatchItem(batch_id=batch.id, row_number=2, external_id="ext-1", ticket_id=ticket.id, status="succeeded"))
        batch_id = batch.id

    with TestClient(create_app(engine=engine, primary=RiskModel())) as api:
        login = api.post("/v1/auth/login", json={"email": "submitter@example.test", "password": "UserPassword123"})
        headers = {"Authorization": "Bearer " + login.json()["token"]}
        response = api.get(f"/v1/batches/{batch_id}/download", headers=headers)
        assert response.status_code == 200
        assert "review_reasons" in response.text.splitlines()[0]
        assert "supplier_master_change|low_confidence" in response.text.splitlines()[1]



def test_deleting_current_prediction_removes_pending_review_item():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    with db.new_session(engine) as session, session.begin():
        bootstrap_admin(session, "admin@example.test", "AdminPassword123")

    with TestClient(create_app(engine=engine, primary=RiskModel())) as api:
        assert api.post("/v1/auth/register", json={"email": "submitter@example.test", "password": "UserPassword123"}).status_code == 200
        assert api.post("/v1/auth/register", json={"email": "reviewer@example.test", "password": "UserPassword123"}).status_code == 200

        def login(email, password):
            response = api.post("/v1/auth/login", json={"email": email, "password": password})
            assert response.status_code == 200
            return {"Authorization": "Bearer " + response.json()["token"]}

        submitter = login("submitter@example.test", "UserPassword123")
        reviewer = login("reviewer@example.test", "UserPassword123")
        admin = login("admin@example.test", "AdminPassword123")
        result = api.post("/v1/classifications", json={"text": "Change the supplier bank details"}, headers=submitter)
        assert result.status_code == 200 and result.json()["review_status"] == "pending_review"
        prediction_id = result.json()["prediction_id"]
        assert len(api.get("/v1/reviews/queue", headers=reviewer).json()) == 1
        assert api.delete(f"/v1/admin/predictions/{prediction_id}", headers=admin).status_code == 200
        assert api.get("/v1/reviews/queue", headers=reviewer).json() == []
        current = api.get("/v1/classifications", headers=submitter).json()[0]
        assert current["review_status"] == "pending_reclassification"
        history = api.get("/v1/admin/history", headers=admin).json()
        assert any(row["id"] == prediction_id for row in history["prediction_history"])

        ticket_id = current["ticket_id"]
        assert api.post(f"/v1/admin/tickets/{ticket_id}/reclassify", headers=reviewer).status_code == 403
        reclassified = api.post(f"/v1/admin/tickets/{ticket_id}/reclassify", headers=admin)
        assert reclassified.status_code == 200, reclassified.text
        assert reclassified.json()["review_status"] == "pending_review"
        assert reclassified.json()["prediction_id"] != prediction_id
        assert len(api.get("/v1/reviews/queue", headers=reviewer).json()) == 1
        assert api.post(f"/v1/admin/tickets/{ticket_id}/reclassify", headers=admin).status_code == 409
        assert api.post("/v1/admin/tickets/missing/reclassify", headers=admin).status_code == 404
        audit_actions = [row["action"] for row in api.get("/v1/admin/history", headers=admin).json()["audit_events"]]
        assert "ticket.reclassify" in audit_actions


def test_admin_ticket_stays_pending_without_model_until_reclassified(monkeypatch):
    monkeypatch.delenv("INVOICEOPS_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("INVOICEOPS_FALLBACK_MODEL", raising=False)
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    with db.new_session(engine) as session, session.begin():
        bootstrap_admin(session, "admin@example.test", "AdminPassword123")
    app = create_app(engine=engine)
    with TestClient(app) as api:
        login = api.post("/v1/auth/login", json={"email": "admin@example.test", "password": "AdminPassword123"})
        admin = {"Authorization": "Bearer " + login.json()["token"]}
        created = api.post("/v1/admin/tickets", json={"text": "Please change the supplier bank account"}, headers=admin)
        assert created.status_code == 200, created.text
        assert created.json()["review_status"] == "pending_reclassification" and "prediction_id" not in created.json()
        ticket_id = created.json()["ticket_id"]
        assert api.post(f"/v1/admin/tickets/{ticket_id}/reclassify", headers=admin).status_code == 503
        row = next(item for item in api.get("/v1/admin/tickets", headers=admin).json() if item["ticket_id"] == ticket_id)
        assert row["review_status"] == "pending_reclassification"

        app.state.primary = RiskModel()
        reclassified = api.post(f"/v1/admin/tickets/{ticket_id}/reclassify", headers=admin)
        assert reclassified.status_code == 200, reclassified.text
        assert reclassified.json()["review_status"] == "pending_review"
        assert reclassified.json()["model_version"] == "approved-test-risk-model"



def test_admin_user_ticket_prediction_and_batch_crud():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    with db.new_session(engine) as session, session.begin():
        bootstrap_admin(session, "admin@example.test", "AdminPassword123")

    with TestClient(create_app(engine=engine, primary=RiskModel())) as api:
        admin_login = api.post("/v1/auth/login", json={"email": "admin@example.test", "password": "AdminPassword123"})
        admin = {"Authorization": "Bearer " + admin_login.json()["token"]}

        created_user = api.post("/v1/admin/users", json={"email": "managed@example.test", "password": "ManagedPass123"}, headers=admin)
        assert created_user.status_code == 200
        user_id = created_user.json()["id"]
        assert created_user.json()["role"] == "user"
        changed_user = api.patch(f"/v1/admin/users/{user_id}", json={"role": "admin"}, headers=admin)
        assert changed_user.status_code == 200 and changed_user.json()["role"] == "admin"
        assert api.patch(f"/v1/admin/users/{user_id}", json={"active": False}, headers=admin).status_code == 200
        assert api.patch(f"/v1/admin/users/{user_id}", json={"active": True}, headers=admin).status_code == 200
        assert api.patch("/v1/admin/users/missing", json={"role": "user"}, headers=admin).status_code == 404

        created_ticket = api.post("/v1/admin/tickets", json={"text": "Original invoice text"}, headers=admin)
        assert created_ticket.status_code == 200
        assert created_ticket.json()["review_status"] == "pending_review" and created_ticket.json()["prediction_id"]
        ticket_id = created_ticket.json()["ticket_id"]
        first_prediction = created_ticket.json()["prediction_id"]
        revised_ticket = api.patch(f"/v1/admin/tickets/{ticket_id}", json={"text": "Revised invoice text"}, headers=admin)
        assert revised_ticket.status_code == 200
        assert revised_ticket.json()["prediction_id"] != first_prediction
        ticket_row = next(row for row in api.get("/v1/admin/tickets", headers=admin).json() if row["ticket_id"] == ticket_id)
        assert ticket_row["content_version"] == 2 and ticket_row["review_status"] == "pending_review"
        assert api.patch(f"/v1/admin/tickets/{ticket_id}", json={"text": "   "}, headers=admin).status_code == 422
        assert api.post(f"/v1/admin/tickets/{ticket_id}/reclassify", headers=admin).status_code == 409
        assert api.delete(f"/v1/admin/tickets/{ticket_id}", headers=admin).status_code == 200

        assert api.post("/v1/auth/register", json={"email": "submitter@example.test", "password": "SubmitterPass123"}).status_code == 200
        login = api.post("/v1/auth/login", json={"email": "submitter@example.test", "password": "SubmitterPass123"})
        submitter = {"Authorization": "Bearer " + login.json()["token"]}
        classification = api.post("/v1/classifications", json={"text": "Change the supplier payment information"}, headers=submitter)
        prediction_id = classification.json()["prediction_id"]
        duplicate_labels = api.patch(f"/v1/admin/predictions/{prediction_id}", json={"final_labels": ["OTHER_REVIEW", "OTHER_REVIEW"], "comment": "duplicate"}, headers=admin)
        assert duplicate_labels.status_code == 422
        revised_prediction = api.patch(f"/v1/admin/predictions/{prediction_id}", json={"final_labels": ["OTHER_REVIEW"], "comment": "Admin verified"}, headers=admin)
        assert revised_prediction.status_code == 200
        assert revised_prediction.json()["final_labels"] == ["OTHER_REVIEW"]

        batch = api.post("/v1/batches", files={"file": ("items.csv", b"text,external_id\nReview invoice,ext-1\n", "text/csv")}, headers=submitter)
        batch_id = batch.json()["id"]
        renamed_batch = api.patch(f"/v1/admin/batches/{batch_id}", json={"name": "Reviewed file"}, headers=admin)
        assert renamed_batch.status_code == 200 and renamed_batch.json()["name"] == "Reviewed file"
        assert api.delete(f"/v1/admin/batches/{batch_id}", headers=admin).status_code == 200

        history = api.get("/v1/admin/history", headers=admin).json()
        assert any(row["action"] == "ticket.delete" and row["object_id"] == ticket_id for row in history["audit_events"])
        assert any(row["action"] == "prediction.revise" and row["object_id"] == prediction_id for row in history["audit_events"])
        assert any(row["batch_id"] == batch_id for row in history["batch_item_history"])
        assert any(row["action"] == "batch.delete" and row["object_id"] == batch_id for row in history["audit_events"])
