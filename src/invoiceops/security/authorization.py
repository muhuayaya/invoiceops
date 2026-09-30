"""Object authorization for FastAPI routes and application services.

These helpers are the single source of the role/ownership rules. They raise
``PermissionDenied`` (mapped to HTTP 403); state and version conflicts are not
authorization decisions and remain ``StateConflict`` (HTTP 409) in the adapter.
"""

from __future__ import annotations

from invoiceops.adapters.db import BatchJob, PermissionDenied, Ticket, User


def require_admin(actor: User) -> None:
    if not actor.active or actor.role != "admin":
        raise PermissionDenied("administrator required")


def can_view_ticket(actor: User, ticket: Ticket) -> None:
    """Reference rule for single-ticket reads; list queries apply the same filter in SQL."""
    if not actor.active or (actor.role != "admin" and ticket.submitted_by != actor.id):
        raise PermissionDenied("ticket access denied")


def can_download_batch(actor: User, batch: BatchJob) -> None:
    if not actor.active or (actor.role != "admin" and batch.submitted_by != actor.id):
        raise PermissionDenied("batch access denied")


def can_review(actor: User, ticket: Ticket) -> None:
    """Any active user may review any pending ticket, including their own.

    Whether the ticket is still pending and at the expected version is checked
    atomically by ``submit_review`` so stale decisions keep returning 409.
    """
    if not actor.active:
        raise PermissionDenied("ticket cannot be reviewed by this user")
