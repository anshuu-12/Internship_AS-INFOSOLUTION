from fastapi import APIRouter, status
from sqlalchemy import select

from app.dependencies import CurrentUser, DbSession, TeacherUser
from app.models import User
from app.schemas import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def my_profile(user: CurrentUser) -> User:
    return user


@router.get("", response_model=list[UserRead])
def list_users(db: DbSession, _: TeacherUser) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at, User.email)).all())
