import pytest

from invoiceops.adapters.db import BatchJob, PermissionDenied, Ticket, User
from invoiceops.security.authorization import can_download_batch, can_review, can_view_ticket, require_admin


def _user(user_id, role="user", active=True):
    return User(id=user_id, email=f"{user_id}@example.test", password_hash="x", role=role, active=active)


def test_require_admin_rejects_users_and_inactive_admins():
    require_admin(_user("admin", "admin"))
    with pytest.raises(PermissionDenied):
        require_admin(_user("user"))
    with pytest.raises(PermissionDenied):
        require_admin(_user("admin", "admin", active=False))


def test_owner_or_admin_rules_for_tickets_and_batches():
    owner, other, admin = _user("owner"), _user("other"), _user("admin", "admin")
    ticket = Ticket(id="t1", submitted_by="owner", redacted_text="x", review_status="pending_review")
    batch = BatchJob(id="b1", submitted_by="owner")
    for actor in (owner, admin):
        can_view_ticket(actor, ticket)
        can_download_batch(actor, batch)
    with pytest.raises(PermissionDenied):
        can_view_ticket(other, ticket)
    with pytest.raises(PermissionDenied):
        can_download_batch(other, batch)
    with pytest.raises(PermissionDenied):
        can_download_batch(_user("owner", active=False), batch)


def test_can_review_allows_self_review_and_leaves_state_checks_to_the_store():
    owner = _user("owner")
    pending = Ticket(id="t1", submitted_by="owner", redacted_text="x", review_status="pending_review")
    reviewed = Ticket(id="t2", submitted_by="owner", redacted_text="x", review_status="confirmed")
    can_review(owner, pending)
    can_review(_user("other"), pending)
    can_review(owner, reviewed)
    with pytest.raises(PermissionDenied):
        can_review(_user("other", active=False), pending)
