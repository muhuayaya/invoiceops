from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt


ROLES = frozenset({"admin", "reviewer", "observer"})
SEED_USERS = {"admin": ("admin", "admin"), "reviewer": ("reviewer", "reviewer"), "observer": ("observer", "observer")}
DEVELOPMENT_ENVIRONMENTS = {"development", "test"}


@dataclass(frozen=True)
class Principal:
    username: str
    role: str


def secret() -> str:
    configured = os.getenv("INVOICEOPS_JWT_SECRET", "local-development-secret-change-me")
    environment = os.getenv("INVOICEOPS_ENV", "development").lower()
    if environment not in DEVELOPMENT_ENVIRONMENTS and (
        len(configured) < 32 or configured == "local-development-secret-change-me" or configured == "change-me-in-development"
    ):
        raise RuntimeError("INVOICEOPS_JWT_SECRET must be a random value of at least 32 characters outside development")
    return configured


def issue_token(username: str, role: str, *, ttl_minutes: int = 60) -> str:
    if role not in ROLES:
        raise ValueError("unknown role")
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": username, "role": role, "iat": now, "exp": now + timedelta(minutes=ttl_minutes)}, secret(), algorithm="HS256")


def decode_token(token: str) -> Principal:
    payload = jwt.decode(token, secret(), algorithms=["HS256"])
    username, role = str(payload.get("sub", "")), str(payload.get("role", ""))
    if not username or role not in ROLES:
        raise ValueError("invalid principal")
    return Principal(username, role)


def authenticate_seed_user(username: str, password: str) -> Principal | None:
    if os.getenv("INVOICEOPS_ENV", "development").lower() not in DEVELOPMENT_ENVIRONMENTS:
        return None
    expected = SEED_USERS.get(username)
    if expected and expected[1] == password:
        return Principal(username, expected[0])
    return None


def can(principal: Principal, required: set[str]) -> bool:
    return principal.role == "admin" or principal.role in required
