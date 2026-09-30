"""Password, bearer-session and administrator operations.

Every function uses the caller's transaction. The caller commits only after the
whole request succeeds. Tokens are returned once and only their digest is kept.
"""

from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timedelta, timezone
from getpass import getpass
from hashlib import sha256
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from invoiceops.adapters.db import LoginSession, NotFoundError, PermissionDenied, User, audit, get_engine, new_session, utcnow


_hasher = PasswordHasher()


class AuthenticationFailed(PermissionDenied):
    pass


def _email(value: str) -> str:
    email = value.strip().casefold()
    if len(email) > 255 or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError("invalid email")
    return email


PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 64
PASSWORD_RULE = "password must be 12-64 characters of English letters and digits only, with at least one letter and one digit"
_PASSWORD_PATTERN = re.compile(r"(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9]{12,64}")


def password_is_valid(value: str) -> bool:
    """New passwords: 12-64 ASCII letters/digits, at least one of each; no spaces or symbols."""
    return isinstance(value, str) and _PASSWORD_PATTERN.fullmatch(value) is not None


def _password(value: str) -> str:
    """Validate a password being set. Login never calls this, so older passwords keep working."""
    if not password_is_valid(value):
        raise ValueError(PASSWORD_RULE)
    return value


def register_user(session: Session, email: str, password: str, *, role: str | None = None) -> User:
    """Self-registration always creates a normal user; requested role is ignored."""
    user = User(email=_email(email), password_hash=_hasher.hash(_password(password)), role="user")
    session.add(user)
    session.flush()
    audit(session, user.id, "user.register", "user", user.id)
    return user


def login_user(session: Session, email: str, password: str, *, ttl: timedelta = timedelta(hours=12)) -> tuple[User, str]:
    try:
        normalized = _email(email)
    except ValueError as exc:
        raise AuthenticationFailed("invalid credentials") from exc
    user = session.scalar(select(User).where(User.email == normalized))
    if user is None or not user.active:
        raise AuthenticationFailed("invalid credentials")
    try:
        _hasher.verify(user.password_hash, password)
    except (VerifyMismatchError, VerificationError) as exc:
        raise AuthenticationFailed("invalid credentials") from exc
    token = secrets.token_urlsafe(32)
    session.add(LoginSession(user_id=user.id, token_hash=sha256(token.encode()).hexdigest(), expires_at=utcnow() + ttl))
    audit(session, user.id, "session.login", "user", user.id)
    return user, token


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def authenticate_token(session: Session, token: str) -> User:
    if not token:
        raise AuthenticationFailed("missing session")
    stored = session.scalar(select(LoginSession).where(LoginSession.token_hash == sha256(token.encode()).hexdigest()))
    if stored is None or stored.revoked_at is not None or _aware(stored.expires_at) <= utcnow():
        raise AuthenticationFailed("invalid session")
    user = session.get(User, stored.user_id)
    if user is None or not user.active:
        raise AuthenticationFailed("invalid session")
    return user


def logout_user(session: Session, token: str) -> None:
    stored = session.scalar(select(LoginSession).where(LoginSession.token_hash == sha256(token.encode()).hexdigest()))
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = utcnow()
        audit(session, stored.user_id, "session.logout", "user", stored.user_id)


def bootstrap_admin(session: Session, email: str, password: str) -> User:
    """Create exactly one first administrator from a local CLI invocation.

    PostgreSQL's table lock serializes concurrent attempts against an empty DB.
    """
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("LOCK TABLE users IN EXCLUSIVE MODE"))
    if session.scalar(select(User.id).where(User.role == "admin").limit(1)) is not None:
        raise PermissionDenied("an administrator already exists")
    user = User(email=_email(email), password_hash=_hasher.hash(_password(password)), role="admin")
    session.add(user)
    session.flush()
    audit(session, user.id, "admin.bootstrap", "user", user.id)
    return user


def _admin(session: Session, actor_id: str) -> User:
    actor = session.get(User, actor_id)
    if actor is None or not actor.active or actor.role != "admin":
        raise PermissionDenied("administrator required")
    return actor


def _protect_last_admin(session: Session, target: User) -> None:
    if target.role != "admin" or not target.active:
        return
    admins = list(session.scalars(select(User).where(User.role == "admin", User.active.is_(True)).order_by(User.id).with_for_update()))
    if len(admins) <= 1:
        raise PermissionDenied("cannot remove the last active administrator")


def create_user(session: Session, actor_id: str, email: str, password: str, *, role: str = "user") -> User:
    _admin(session, actor_id)
    if role not in ("user", "admin"):
        raise ValueError("invalid role")
    user = User(email=_email(email), password_hash=_hasher.hash(_password(password)), role=role)
    session.add(user)
    session.flush()
    audit(session, actor_id, "user.create", "user", user.id, {"role": role})
    return user


def set_user_role(session: Session, actor_id: str, target_id: str, role: str) -> User:
    _admin(session, actor_id)
    if role not in ("user", "admin"):
        raise ValueError("invalid role")
    target = session.get(User, target_id)
    if target is None:
        raise NotFoundError("user not found")
    if target.role == "admin" and role != "admin":
        _protect_last_admin(session, target)
    target.role = role
    audit(session, actor_id, "user.role", "user", target.id, {"role": role})
    return target


def set_user_active(session: Session, actor_id: str, target_id: str, active: bool) -> User:
    _admin(session, actor_id)
    target = session.get(User, target_id)
    if target is None:
        raise NotFoundError("user not found")
    if target.active and not active:
        _protect_last_admin(session, target)
    target.active = active
    if not active:
        session.execute(delete(LoginSession).where(LoginSession.user_id == target.id))
    audit(session, actor_id, "user.active", "user", target.id, {"active": active})
    return target


def delete_user(session: Session, actor_id: str, target_id: str) -> None:
    _admin(session, actor_id)
    target = session.get(User, target_id)
    if target is None:
        raise NotFoundError("user not found")
    _protect_last_admin(session, target)
    audit(session, actor_id, "user.delete", "user", target.id)
    session.delete(target)


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the first InvoiceOps administrator on this host")
    parser.add_argument("--database-url", default=os.getenv("INVOICEOPS_DATABASE_URL"), required=not bool(os.getenv("INVOICEOPS_DATABASE_URL")))
    parser.add_argument("--email", required=True)
    parser.add_argument("--password-env", help="Read the administrator password from this environment variable")
    parser.add_argument("--if-missing", action="store_true", help="Succeed when an administrator already exists")
    args = parser.parse_args()
    if args.password_env:
        password = os.getenv(args.password_env)
        if password is None:
            parser.error(f"environment variable {args.password_env} is not set")
    else:
        password = getpass("New administrator password: ")
        confirmation = getpass("Confirm password: ")
        if password != confirmation:
            parser.error("passwords do not match")
    engine = get_engine(args.database_url)
    try:
        with new_session(engine) as session, session.begin():
            bootstrap_admin(session, args.email, password)
    except PermissionDenied as exc:
        if not args.if_missing or str(exc) != "an administrator already exists":
            raise
        print("Administrator already exists")
        return
    print("First administrator created")


if __name__ == "__main__":
    main()
