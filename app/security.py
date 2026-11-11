import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.config import get_settings


password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(*, user_id: str, session_id: str, token_type: str, expires_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": user_id,
        "sid": session_id,
        "type": token_type,
        "iat": now,
        "exp": expires_at,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(claims, get_settings().jwt_secret.get_secret_value(), algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        get_settings().jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        options={"require": ["exp", "iat", "sub", "sid", "type"]},
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def refresh_expiry() -> datetime:
    return utc_now() + timedelta(days=get_settings().refresh_token_days)


def access_expiry() -> datetime:
    return utc_now() + timedelta(minutes=get_settings().access_token_minutes)
