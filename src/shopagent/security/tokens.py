from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Literal

PrincipalRole = Literal["user", "admin", "service"]


class TokenError(ValueError):
    """Raised when a ShopAgent access token is malformed or not trustworthy."""


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    role: PrincipalRole
    agent: str | None = None


def issue_token(
    secret: str,
    *,
    subject: str,
    role: PrincipalRole,
    agent: str | None = None,
    ttl_seconds: int = 3600,
    now: int | None = None,
) -> str:
    """Issue a compact HMAC token for private deployments and service-to-service calls."""
    if not secret or not subject or ttl_seconds <= 0:
        raise ValueError("secret, subject and a positive ttl_seconds are required")
    issued_at = int(time.time()) if now is None else now
    payload = {
        "aud": "shopagent",
        "sub": subject,
        "role": role,
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
    }
    if agent:
        payload["agent"] = agent
    encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _sign(secret, f"v1.{encoded}".encode())
    return f"v1.{encoded}.{signature}"


def verify_token(
    secret: str,
    token: str,
    *,
    expected_role: PrincipalRole | None = None,
    now: int | None = None,
) -> Principal:
    try:
        version, encoded, provided_signature = token.split(".", 2)
    except ValueError as exc:
        raise TokenError("malformed token") from exc
    if version != "v1":
        raise TokenError("unsupported token version")
    expected_signature = _sign(secret, f"{version}.{encoded}".encode())
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise TokenError("invalid token signature")
    try:
        payload = json.loads(_decode(encoded))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TokenError("invalid token payload") from exc
    if payload.get("aud") != "shopagent":
        raise TokenError("invalid token audience")
    current_time = int(time.time()) if now is None else now
    if not isinstance(payload.get("exp"), int) or payload["exp"] <= current_time:
        raise TokenError("token expired")
    role = payload.get("role")
    if role not in {"user", "admin", "service"}:
        raise TokenError("invalid token role")
    if expected_role and role != expected_role:
        raise TokenError("token role is not allowed")
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 128:
        raise TokenError("invalid token subject")
    agent = payload.get("agent")
    if agent is not None and (not isinstance(agent, str) or not agent or len(agent) > 128):
        raise TokenError("invalid token agent")
    return Principal(subject=subject, role=role, agent=agent)


def _sign(secret: str, message: bytes) -> str:
    return _encode(hmac.new(secret.encode(), message, hashlib.sha256).digest())


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode()
