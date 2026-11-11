import os
from datetime import timedelta

os.environ.setdefault("JWT_SECRET", "test-only-secret-key-that-is-at-least-32-characters")

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.database import Base, get_db
from app.models import User
from app.security import hash_password, utc_now


@pytest.fixture
def client(monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(main_module, "engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    main_module.app.dependency_overrides[get_db] = override_get_db
    with TestingSessionLocal.begin() as db:
        db.add_all(
            [
                User(
                    email="teacher@example.com",
                    full_name="Test Teacher",
                    password_hash=hash_password("teacher-pass-123"),
                    role="teacher",
                ),
                User(
                    email="student@example.com",
                    full_name="Test Student",
                    password_hash=hash_password("student-pass-123"),
                    role="student",
                ),
                User(
                    email="other@example.com",
                    full_name="Other Student",
                    password_hash=hash_password("other-pass-123"),
                    role="student",
                ),
            ]
        )
    with TestClient(main_module.app) as test_client:
        yield test_client
    main_module.app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_teacher_assigns_task_student_updates_and_teacher_sees_progress(client: TestClient):
    teacher_tokens = login(client, "teacher@example.com", "teacher-pass-123")
    student_tokens = login(client, "student@example.com", "student-pass-123")
    teacher = bearer(teacher_tokens["access_token"])
    student = bearer(student_tokens["access_token"])

    student_id = client.get("/auth/me", headers=student).json()["id"]
    created = client.post(
        "/tasks",
        headers=teacher,
        json={"title": "Write report", "description": "First draft", "assigned_to": student_id},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    assert [task["id"] for task in client.get("/tasks", headers=student).json()] == [task_id]
    updated = client.patch(
        f"/tasks/{task_id}",
        headers=student,
        json={"status": "in progress", "work_notes": "Outline complete"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "in progress"
    assert updated.json()["work_notes"] == "Outline complete"
    progress = client.get("/tasks/progress", headers=teacher)
    assert progress.status_code == 200
    assert progress.json()["total"] == 1
    assert progress.json()["in_progress"] == 1


def test_role_permissions_and_isolation(client: TestClient):
    teacher_tokens = login(client, "teacher@example.com", "teacher-pass-123")
    student_tokens = login(client, "student@example.com", "student-pass-123")
    teacher = bearer(teacher_tokens["access_token"])
    student = bearer(student_tokens["access_token"])
    student_id = client.get("/auth/me", headers=student).json()["id"]
    task = client.post(
        "/tasks",
        headers=teacher,
        json={"title": "Private task", "assigned_to": student_id},
    ).json()

    assert client.get("/users", headers=student).status_code == 403
    assert client.get("/tasks/progress", headers=student).status_code == 403
    assert client.patch(
        f"/tasks/{task['id']}", headers=student, json={"assigned_to": None}
    ).status_code == 403
    assert client.delete(f"/tasks/{task['id']}", headers=student).status_code == 403

    other_tokens = login(client, "other@example.com", "other-pass-123")
    other = bearer(other_tokens["access_token"])
    assert client.get(f"/tasks/{task['id']}", headers=other).status_code == 404
    assert client.patch(
        f"/tasks/{task['id']}", headers=other, json={"status": "done"}
    ).status_code == 404


def test_invalid_login_missing_token_and_missing_records(client: TestClient):
    assert client.post(
        "/auth/login", json={"email": "teacher@example.com", "password": "wrong-pass"}
    ).status_code == 401
    assert client.get("/tasks").status_code == 401
    teacher = bearer(login(client, "teacher@example.com", "teacher-pass-123")["access_token"])
    assert client.get("/tasks/not-a-task", headers=teacher).status_code == 404
    assert client.post("/tasks", headers=teacher, json={"title": ""}).status_code == 422


def test_registration_creates_student_only_and_hashes_password(client: TestClient):
    payload = {
        "email": "new-student@example.com",
        "full_name": "  New Student  ",
        "password": "new-student-password",
    }
    elevated_registration = client.post(
        "/auth/register",
        json={**payload, "role": "teacher"},
    )
    assert elevated_registration.status_code == 422
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201
    registered_user = response.json()
    assert registered_user["role"] == "student"
    assert registered_user["full_name"] == "New Student"
    assert "password_hash" not in registered_user
    assert login(client, "new-student@example.com", "new-student-password")
    assert client.post(
        "/auth/register",
        json={
            "email": "new-student@example.com",
            "full_name": "New Student",
            "password": "new-student-password",
        },
    ).status_code == 409
    assert client.post(
        "/auth/register",
        json={"email": "another@example.com", "full_name": " ", "password": "long-enough"}
    ).status_code == 422


def test_refresh_rotates_tokens_and_logout_revokes_session(client: TestClient):
    tokens = login(client, "student@example.com", "student-pass-123")
    refreshed = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401

    logout = client.post("/auth/logout", json={"refresh_token": new_tokens["refresh_token"]})
    assert logout.status_code == 204
    assert client.get("/auth/me", headers=bearer(new_tokens["access_token"])).status_code == 401
    assert client.post(
        "/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    ).status_code == 401


def test_expired_access_and_refresh_tokens_are_rejected(client: TestClient):
    expired_tokens = {}
    for token_type in ("access", "refresh"):
        expired_tokens[token_type] = jwt.encode(
            {
                "sub": "some-user",
                "sid": "some-session",
                "type": token_type,
                "iat": utc_now() - timedelta(minutes=5),
                "exp": utc_now() - timedelta(minutes=1),
            },
            os.environ["JWT_SECRET"],
            algorithm="HS256",
        )
    assert client.get("/auth/me", headers=bearer(expired_tokens["access"])).status_code == 401
    assert client.post(
        "/auth/refresh", json={"refresh_token": expired_tokens["refresh"]}
    ).status_code == 401
