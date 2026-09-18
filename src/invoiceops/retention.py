from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class RetentionPolicy:
    sanitized_text_days: int = 30
    suggestion_days: int = 30
    audit_days: int = 365


def cleanup_in_memory(repository, policy: RetentionPolicy | None = None) -> dict[str, int | str]:
    policy = policy or RetentionPolicy()
    cutoff = datetime.now(timezone.utc) - timedelta(days=policy.sanitized_text_days)
    cleaned = 0
    for ticket in repository.tickets.values():
        if ticket.created_at < cutoff and ticket.text:
            ticket.text = "[REDACTED_AFTER_RETENTION]"
            persist_ticket = getattr(repository, "persist_ticket", None)
            if persist_ticket is not None:
                persist_ticket(ticket)
            cleaned += 1
    return {"cleaned_tickets": cleaned, "policy": "retention-v1", "audit_proof": "cleanup event must be appended by caller"}
