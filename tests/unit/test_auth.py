import pytest

from invoiceops.security.auth import authenticate_seed_user, can, decode_token, issue_token


def test_seed_roles_and_jwt_round_trip() -> None:
    principal = authenticate_seed_user("reviewer", "reviewer")
    assert principal is not None
    token = issue_token(principal.username, principal.role)
    assert decode_token(token) == principal
    assert can(principal, {"reviewer"})
    assert not can(principal, {"observer"})


def test_invalid_role_is_rejected() -> None:
    with pytest.raises(ValueError):
        issue_token("user", "unknown")


def test_seed_users_and_default_secret_are_disabled_outside_development(monkeypatch) -> None:
    monkeypatch.setenv("INVOICEOPS_ENV", "production")
    monkeypatch.setenv("INVOICEOPS_JWT_SECRET", "change-me-in-development")
    assert authenticate_seed_user("admin", "admin") is None
    with pytest.raises(RuntimeError, match="INVOICEOPS_JWT_SECRET"):
        issue_token("admin", "admin")
