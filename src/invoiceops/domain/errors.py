class DomainError(Exception):
    """Base error for business invariant violations."""


class InvalidTransition(DomainError):
    """Raised when an aggregate receives an unsupported state transition."""


class ValidationError(DomainError):
    """Raised when a value violates a domain constraint."""
