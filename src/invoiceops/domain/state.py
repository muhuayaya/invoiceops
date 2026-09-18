"""Small explicit state transition helpers used by application services."""

from .entities import ModelStatus, Ticket, TicketStatus


def transition_ticket(ticket: Ticket, target: TicketStatus) -> Ticket:
    ticket.transition_to(target)
    return ticket


def activate_model(version):
    return version.activate()


def rollback_model(version):
    return version.rollback()


__all__ = ["ModelStatus", "TicketStatus", "activate_model", "rollback_model", "transition_ticket"]
