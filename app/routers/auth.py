import hmac
from datetime import datetime
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import CurrentUser
from app.models import AuthSession, User
from app.schemas import LoginRequest, RefreshRequest, TokenResponse, UserRead, UserRegister
from app.security import access_expiry, as_utc, create_token, decode_token, hash_password
from app.security import hash_refresh_token, refresh_expiry, utc_now, verify_password

router = APIRouter(prefix="/auth", tags=["authentication"])
DbSession = Annotated[Session, Depends(get_db)]


def issue_tokens(user_id: str, session_id: str, expires_at: datetime) -> TokenResponse:
    access_token = create_token(
        user_id=user_id,
        session_id=session_id,
        token_type="access",
        expires_at=access_expiry(),
    )
    refresh_token = create_token(
        user_id=user_id,
        session_id=session_id,
        token_type="refresh",
        expires_at=expires_at,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_in=get_settings().access_token_minutes * 60,
    )


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: DbSession) -> User:
    email = str(payload.email).lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")
    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role="student",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered") from None
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expires_at = refresh_expiry()
    auth_session = AuthSession(user_id=user.id, expires_at=expires_at, refresh_token_hash="")
    db.add(auth_session)
    db.flush()
    tokens = issue_tokens(user.id, auth_session.id, expires_at)
    auth_session.refresh_token_hash = hash_refresh_token(tokens.refresh_token)
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        claims = decode_token(payload.refresh_token)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        raise unauthorized from None
    if claims.get("type") != "refresh":
        raise unauthorized

    auth_session = db.scalar(
        select(AuthSession)
        .where(AuthSession.id == claims.get("sid"))
        .with_for_update()
    )
    if (
        auth_session is None
        or auth_session.user_id != claims.get("sub")
        or auth_session.revoked_at is not None
        or as_utc(auth_session.expires_at) <= utc_now()
        or not hmac.compare_digest(
            auth_session.refresh_token_hash, hash_refresh_token(payload.refresh_token)
        )
    ):
        raise unauthorized

    tokens = issue_tokens(auth_session.user_id, auth_session.id, auth_session.expires_at)
    auth_session.refresh_token_hash = hash_refresh_token(tokens.refresh_token)
    db.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest, db: DbSession) -> None:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        claims = decode_token(payload.refresh_token)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        raise unauthorized from None
    if claims.get("type") != "refresh":
        raise unauthorized
    auth_session = db.get(AuthSession, claims.get("sid"))
    if (
        auth_session is None
        or auth_session.user_id != claims.get("sub")
        or auth_session.revoked_at is not None
        or as_utc(auth_session.expires_at) <= utc_now()
        or not hmac.compare_digest(
            auth_session.refresh_token_hash, hash_refresh_token(payload.refresh_token)
        )
    ):
        raise unauthorized
    auth_session.revoked_at = utc_now()
    db.commit()


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> User:
    return user
