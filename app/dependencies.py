from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuthSession, User
from app.security import as_utc, decode_token, utc_now


bearer_scheme = HTTPBearer(auto_error=False)
DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        claims = decode_token(credentials.credentials)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        raise unauthorized from None
    if claims.get("type") != "access":
        raise unauthorized

    auth_session = db.scalar(
        select(AuthSession).where(AuthSession.id == claims.get("sid"))
    )
    if (
        auth_session is None
        or auth_session.user_id != claims.get("sub")
        or auth_session.revoked_at is not None
        or as_utc(auth_session.expires_at) <= utc_now()
    ):
        raise unauthorized

    user = db.get(User, claims.get("sub"))
    if user is None:
        raise unauthorized
    return user


def require_teacher(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != "teacher":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher role required",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
TeacherUser = Annotated[User, Depends(require_teacher)]
