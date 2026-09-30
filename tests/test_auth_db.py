from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import Session

from invoiceops.adapters.db import (
    AuditEvent,
    Base,
    BatchJob,
    BatchItem,
    PermissionDenied,
    Prediction,
    PredictionHistory,
    ReviewDecision,
    StateConflict,
    Ticket,
    TicketVersion,
    User,
    add_prediction,
    admin_revise_prediction,
    create_ticket,
    delete_batch,
    delete_prediction,
    delete_ticket,
    get_batch_for_download,
    list_review_queue,
    list_submission_history,
    submit_review,
)
from invoiceops.security.auth import (
    AuthenticationFailed,
    authenticate_token,
    bootstrap_admin,
    delete_user,
    login_user,
    logout_user,
    register_user,
    set_user_active,
    set_user_role,
)


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _people(session: Session):
    admin = bootstrap_admin(session, "admin@example.com", "StrongPassword1")
    submitter = register_user(session, "submit@example.com", "StrongPassword2", role="admin")
    reviewer = register_user(session, "review@example.com", "StrongPassword3")
    session.flush()
    return admin, submitter, reviewer


def test_registration_session_and_disabled_account(engine):
    with Session(engine) as db, db.begin():
        admin, user, _ = _people(db)
        assert user.role == "user"
        assert user.password_hash != "StrongPassword2"
        assert user.password_hash.startswith("$argon2")
        with pytest.raises(PermissionDenied):
            bootstrap_admin(db, "second@example.com", "StrongPassword4")
        logged_in, token = login_user(db, "SUBMIT@example.com", "StrongPassword2")
        assert logged_in.id == user.id
        assert authenticate_token(db, token).id == user.id
        set_user_active(db, admin.id, user.id, False)
        with pytest.raises(AuthenticationFailed):
            authenticate_token(db, token)
        assert not any(token in str(e.details) for e in db.scalars(select(AuditEvent)))


def test_bootstrap_first_admin_after_regular_user_registration(engine):
    with Session(engine) as db, db.begin():
        user = register_user(db, "first-user@example.com", "StrongPassword1")
        admin = bootstrap_admin(db, "admin@example.com", "StrongPassword2")

        assert user.role == "user"
        assert admin.role == "admin"
        assert login_user(db, user.email, "StrongPassword1")[0].id == user.id
        assert login_user(db, admin.email, "StrongPassword2")[0].id == admin.id


def test_logout_role_change_and_last_administrator(engine):
    with Session(engine) as db, db.begin():
        admin, user, reviewer = _people(db)
        _, token = login_user(db, user.email, "StrongPassword2")
        logout_user(db, token)
        with pytest.raises(AuthenticationFailed):
            authenticate_token(db, token)
        with pytest.raises(PermissionDenied):
            set_user_role(db, user.id, user.id, "admin")
        with pytest.raises(PermissionDenied):
            set_user_role(db, admin.id, admin.id, "user")
        with pytest.raises(PermissionDenied):
            set_user_active(db, admin.id, admin.id, False)
        with pytest.raises(PermissionDenied):
            delete_user(db, admin.id, admin.id)
        set_user_role(db, admin.id, reviewer.id, "admin")
        set_user_role(db, admin.id, admin.id, "user")
        assert db.get(User, reviewer.id).role == "admin"


def test_review_cas_self_review_and_physical_delete_history(engine):
    with Session(engine) as db, db.begin():
        admin, submitter, reviewer = _people(db)
        ticket = create_ticket(db, submitter.id, "redacted invoice text")
        prediction = add_prediction(db, ticket.id, probabilities={"OTHER_REVIEW": 0.8}, labels=["OTHER_REVIEW"], model_version="model-1", threshold_version="threshold-1", taxonomy_version="taxonomy-1", review_reason="low confidence")
        db.flush()
        ticket_id, prediction_id, version = ticket.id, prediction.id, ticket.state_version
        decision = submit_review(db, ticket_id, submitter.id, expected_version=version, decision="corrected", final_labels=["PAYMENT_STATUS"], comment="human review")
        db.flush()
        assert ticket.review_status == "corrected"
        assert prediction.labels == ["OTHER_REVIEW"]
        assert decision.original_labels == ["OTHER_REVIEW"]
        with pytest.raises(StateConflict):
            submit_review(db, ticket_id, admin.id, expected_version=version, decision="confirmed", final_labels=["OTHER_REVIEW"])
        delete_ticket(db, ticket_id, admin.id)
    with Session(engine) as db:
        assert db.get(Ticket, ticket_id) is None
        assert db.get(Prediction, prediction_id) is None
        assert db.get(PredictionHistory, prediction_id) is not None
        assert db.scalar(select(TicketVersion).where(TicketVersion.ticket_id == ticket_id)) is not None
        assert db.scalar(select(ReviewDecision).where(ReviewDecision.ticket_id == ticket_id)).comment == "human review"
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "ticket.delete", AuditEvent.object_id == ticket_id)) is not None


def test_object_scopes_and_deleted_user_audit(engine):
    with Session(engine) as db, db.begin():
        admin, owner, reviewer = _people(db)
        ticket = create_ticket(db, owner.id, "redacted")
        batch = BatchJob(submitted_by=owner.id)
        db.add(batch)
        db.flush()
        ticket_id, batch_id, owner_id = ticket.id, batch.id, owner.id
        assert [t.id for t in list_submission_history(db, owner)] == [ticket_id]
        assert list_submission_history(db, reviewer) == []
        assert [t.id for t in list_review_queue(db, reviewer)] == [ticket_id]
        assert [t.id for t in list_review_queue(db, owner)] == [ticket_id]
        with pytest.raises(PermissionDenied):
            get_batch_for_download(db, batch_id, reviewer)
        assert get_batch_for_download(db, batch_id, owner).id == batch_id
        delete_user(db, admin.id, owner_id)
    with Session(engine) as db:
        assert db.get(User, owner_id) is None
        assert db.get(Ticket, ticket_id).submitted_by == owner_id
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "user.delete", AuditEvent.object_id == owner_id)) is not None


def test_prediction_and_batch_physical_delete(engine):
    with Session(engine) as db, db.begin():
        admin, owner, _ = _people(db)
        ticket = create_ticket(db, owner.id, "redacted")
        prediction = add_prediction(db, ticket.id, probabilities={"OTHER_REVIEW": 0.8}, labels=["OTHER_REVIEW"], model_version="m", threshold_version="t", taxonomy_version="v")
        batch = BatchJob(submitted_by=owner.id)
        db.add(batch)
        db.flush()
        prediction_id, batch_id = prediction.id, batch.id
        delete_prediction(db, prediction_id, admin.id)
        delete_batch(db, batch_id, admin.id)
    with Session(engine) as db:
        assert db.get(Prediction, prediction_id) is None
        assert db.get(PredictionHistory, prediction_id) is not None
        assert db.get(BatchJob, batch_id) is None
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "batch.delete", AuditEvent.object_id == batch_id)) is not None


def test_initial_migration_upgrade_and_rollback(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "migration.sqlite"
    monkeypatch.setenv("INVOICEOPS_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    config = Config(str(Path(__file__).resolve().parents[1] / "infra" / "migrations" / "alembic.ini"))
    command.upgrade(config, "0001_initial")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    assert "batch_item_history" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "batch_item_history" in inspect(engine).get_table_names()
    assert {"users", "tickets", "prediction_history", "review_decisions", "audit_events"}.issubset(inspect(engine).get_table_names())
    assert "fallback_reason" in {column["name"] for column in inspect(engine).get_columns("predictions")}
    assert "fallback_reason" in {column["name"] for column in inspect(engine).get_columns("prediction_history")}
    assert "external_id" in {column["name"] for column in inspect(engine).get_columns("tickets")}
    assert "name" in {column["name"] for column in inspect(engine).get_columns("batch_jobs")}
    assert "review_no_self" not in {item["name"] for item in inspect(engine).get_check_constraints("review_decisions")}
    command.downgrade(config, "0001_initial")
    assert "batch_item_history" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert "users" not in inspect(engine).get_table_names()
    engine.dispose()


def test_user_scoped_idempotency_and_batch_retry_fields(engine):
    with Session(engine) as db, db.begin():
        _, owner, other = _people(db)
        first = create_ticket(db, owner.id, "redacted one", idempotency_key="request-1")
        same = create_ticket(db, owner.id, "redacted one", idempotency_key="request-1")
        another = create_ticket(db, other.id, "redacted two", idempotency_key="request-1")
        assert same.id == first.id
        assert another.id != first.id
        batch = BatchJob(submitted_by=owner.id, total_rows=1)
        db.add(batch)
        db.flush()
        item = BatchItem(batch_id=batch.id, row_number=2, redacted_text="[REDACTED] invoice", error_code="EMPTY_TEXT")
        db.add(item)
        db.flush()
        assert db.get(BatchItem, item.id).redacted_text == "[REDACTED] invoice"
        assert db.get(BatchItem, item.id).error_code == "EMPTY_TEXT"


def test_idempotency_unique_conflict_recovers_transaction(engine, monkeypatch):
    with Session(engine) as db, db.begin():
        _, owner, _ = _people(db)
        owner_id = owner.id
        original = create_ticket(db, owner.id, "redacted", idempotency_key="same")
        db.flush()
        original_scalar = db.scalar
        calls = 0

        def stale_read(statement, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return None
            return original_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(db, "scalar", stale_read)
        retry = create_ticket(db, owner.id, "redacted", idempotency_key="same")
        assert retry.id == original.id
        assert create_ticket(db, owner.id, "another", idempotency_key="another").id != original.id
    with Session(engine) as db:
        assert len(list(db.scalars(select(Ticket).where(Ticket.submitted_by == owner_id)))) == 2


def test_admin_classification_revision_appends_decision(engine):
    with Session(engine) as db, db.begin():
        admin, owner, ordinary = _people(db)
        ticket = create_ticket(db, owner.id, "redacted")
        prediction = add_prediction(db, ticket.id, probabilities={"OTHER_REVIEW": 0.7}, labels=["OTHER_REVIEW"], model_version="m", threshold_version="t", taxonomy_version="v")
        db.flush()
        with pytest.raises(PermissionDenied):
            admin_revise_prediction(db, prediction.id, ordinary.id, ["PAYMENT_STATUS"])
        decision = admin_revise_prediction(db, prediction.id, admin.id, ["PAYMENT_STATUS"], "admin correction")
        db.flush()
        assert prediction.labels == ["OTHER_REVIEW"]
        assert decision.original_labels == ["OTHER_REVIEW"]
        assert decision.final_labels == ["PAYMENT_STATUS"]
        assert ticket.review_status == "corrected"


def test_fallback_reason_is_separate_and_survives_deletion(engine):
    with Session(engine) as db, db.begin():
        admin, owner, _ = _people(db)
        ticket = create_ticket(db, owner.id, "redacted")
        with pytest.raises(ValueError, match="fallback_reason"):
            add_prediction(db, ticket.id, probabilities={"OTHER_REVIEW": 0.6}, labels=["OTHER_REVIEW"], model_version="tfidf-1", threshold_version="t1", taxonomy_version="v1", degraded=True)
        prediction = add_prediction(db, ticket.id, probabilities={"OTHER_REVIEW": 0.6}, labels=["OTHER_REVIEW"], model_version="tfidf-1", threshold_version="t1", taxonomy_version="v1", degraded=True, review_reason="degraded model needs review", fallback_reason="primary inference timed out")
        db.flush()
        assert prediction.review_reason == "degraded model needs review"
        assert prediction.fallback_reason == "primary inference timed out"
        assert ticket.review_status == "pending_review"
        prediction_id = prediction.id
        delete_ticket(db, ticket.id, admin.id)
    with Session(engine) as db:
        history = db.get(PredictionHistory, prediction_id)
        assert history.review_reason == "degraded model needs review"
        assert history.fallback_reason == "primary inference timed out"


@pytest.mark.parametrize("password", ["Abc123def456", "abc123def456", "ABCDEFGHIJK1", "1bcdefghijkl", "A1" * 32])
def test_password_policy_accepts_alphanumeric_with_letter_and_digit(password):
    from invoiceops.security.auth import password_is_valid

    assert password_is_valid(password)


@pytest.mark.parametrize("password", [
    "Abc123def45",          # 11 characters
    "A1" * 32 + "b",        # 65 characters
    "abcdefghijkl",         # no digit
    "123456789012",         # no letter
    "Abc 123def456",        # space
    "Abc123def456 ",        # trailing space
    "Abc123def456!",        # symbol
    "Abc_123def456",        # underscore
    "Abc123def\u4e2d456",  # non-ASCII letter
    "Abc\uff11\uff12\uff13def456",  # full-width digits
])
def test_password_policy_rejects_other_passwords(engine, password):
    from invoiceops.security.auth import password_is_valid

    assert not password_is_valid(password)
    with Session(engine) as db, db.begin():
        with pytest.raises(ValueError, match="letters and digits"):
            register_user(db, "weak@example.com", password)
        with pytest.raises(ValueError, match="letters and digits"):
            bootstrap_admin(db, "weak-admin@example.com", password)


def test_existing_password_outside_new_policy_can_still_log_in(engine):
    from invoiceops.security.auth import _hasher

    with Session(engine) as db, db.begin():
        db.add(User(email="legacy@example.com", password_hash=_hasher.hash("legacy password-123!"), role="user"))
        db.flush()
        user, token = login_user(db, "legacy@example.com", "legacy password-123!")
        assert user.email == "legacy@example.com" and token
