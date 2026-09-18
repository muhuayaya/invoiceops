from uuid import uuid4

import pytest

from invoiceops.domain.entities import ModelStatus, ModelVersion, Ticket, TicketStatus
from invoiceops.domain.errors import InvalidTransition


def test_ticket_rejects_review_before_classification() -> None:
    ticket = Ticket(uuid4(), "request-1", "email", "text", "invoiceops-v1")
    with pytest.raises(InvalidTransition):
        ticket.transition_to(TicketStatus.REVIEWED)


def test_model_can_be_activated_then_rolled_back() -> None:
    model = ModelVersion("m1", "baseline", "thresholds-v1")
    active = model.activate()
    assert active.status == ModelStatus.ACTIVE
    assert active.rollback().status == ModelStatus.ROLLED_BACK
