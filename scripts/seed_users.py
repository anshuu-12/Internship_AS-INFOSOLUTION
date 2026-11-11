import os
import sys

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import User
from app.security import hash_password


def required(name: str, minimum_length: int = 1) -> str:
    value = os.getenv(name, "").strip()
    if len(value) < minimum_length:
        raise ValueError(f"{name} must be provided and be at least {minimum_length} characters")
    return value


def seed_user(db, *, email: str, full_name: str, password: str, role: str) -> str:
    normalized_email = email.lower()
    if db.scalar(select(User.id).where(User.email == normalized_email)) is not None:
        raise ValueError(f"A user with email {normalized_email} already exists; no changes were made")
    user = User(
        email=normalized_email,
        full_name=full_name,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.flush()
    return user.email


def main() -> int:
    try:
        users = [
            {
                "email": required("TEACHER_EMAIL"),
                "full_name": required("TEACHER_NAME"),
                "password": required("TEACHER_PASSWORD", 8),
                "role": "teacher",
            },
            {
                "email": required("STUDENT_EMAIL"),
                "full_name": required("STUDENT_NAME"),
                "password": required("STUDENT_PASSWORD", 8),
                "role": "student",
            },
        ]
        Base.metadata.create_all(bind=engine)
        with SessionLocal.begin() as db:
            emails = [seed_user(db, **user) for user in users]
    except (ValueError, OSError) as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1
    print(f"Created teacher {emails[0]} and student {emails[1]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
