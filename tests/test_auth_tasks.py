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
def client():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)

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

    listed = client.get("/tasks", headers=student).json()
    assert [task["id"] for task in listed["items"]] == [task_id]
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


def test_login_returns_tokens_for_teacher_and_student(client: TestClient):
    teacher_tokens = login(client, "teacher@example.com", "teacher-pass-123")
    student_tokens = login(client, "student@example.com", "student-pass-123")

    for tokens, expected_role in (
        (teacher_tokens, "teacher"),
        (student_tokens, "student"),
    ):
        assert tokens["token_type"] == "bearer"
        assert tokens["access_token"]
        assert tokens["refresh_token"]
        assert tokens["access_expires_in"] > 0
        profile = client.get("/auth/me", headers=bearer(tokens["access_token"]))
        assert profile.status_code == 200
        assert profile.json()["role"] == expected_role


def test_teacher_can_create_assign_update_and_delete_task(client: TestClient):
    teacher = bearer(login(client, "teacher@example.com", "teacher-pass-123")["access_token"])
    student = bearer(login(client, "student@example.com", "student-pass-123")["access_token"])
    student_id = client.get("/auth/me", headers=student).json()["id"]

    created = client.post(
        "/tasks",
        headers=teacher,
        json={
            "title": "  Initial title  ",
            "description": "Initial description",
            "assigned_to": student_id,
        },
    )
    assert created.status_code == 201
    task = created.json()
    assert task["title"] == "Initial title"
    assert task["assigned_to"] == student_id
    assert task["created_by"] == client.get("/auth/me", headers=teacher).json()["id"]

    updated = client.patch(
        f"/tasks/{task['id']}",
        headers=teacher,
        json={"title": "Updated title", "status": "done"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated title"
    assert updated.json()["status"] == "done"

    deleted = client.delete(f"/tasks/{task['id']}", headers=teacher)
    assert deleted.status_code == 204
    assert client.get(f"/tasks/{task['id']}", headers=teacher).status_code == 404


def test_task_listing_paginates_searches_and_filters(client: TestClient):
    teacher = bearer(login(client, "teacher@example.com", "teacher-pass-123")["access_token"])
    students = {
        user["email"]: user["id"]
        for user in client.get("/users", headers=teacher).json()
        if user["role"] == "student"
    }
    student_id = students["student@example.com"]
    other_student_id = students["other@example.com"]

    task_ids = []
    for payload in (
        {"title": "Alpha report", "assigned_to": student_id, "status": "pending"},
        {"title": "Beta report", "assigned_to": student_id, "status": "in progress"},
        {"title": "Gamma notes", "assigned_to": other_student_id, "status": "pending"},
        {"title": "Delta report", "status": "done"},
    ):
        created = client.post(
            "/tasks",
            headers=teacher,
            json={"title": payload["title"], "assigned_to": payload.get("assigned_to")},
        )
        assert created.status_code == 201
        task_ids.append(created.json()["id"])
        if payload["status"] != "pending":
            updated = client.patch(
                f"/tasks/{created.json()['id']}",
                headers=teacher,
                json={"status": payload["status"]},
            )
            assert updated.status_code == 200

    first_page = client.get("/tasks?page=1&limit=2", headers=teacher).json()
    second_page = client.get("/tasks?page=2&limit=2", headers=teacher).json()
    assert first_page["page"] == 1
    assert first_page["limit"] == 2
    assert first_page["total"] == 4
    assert len(first_page["items"]) == 2
    assert second_page["page"] == 2
    assert second_page["total"] == 4
    assert len(second_page["items"]) == 2
    assert {task["id"] for task in first_page["items"]}.isdisjoint(
        task["id"] for task in second_page["items"]
    )

    searched = client.get("/tasks?search=REPORT", headers=teacher).json()
    assert searched["total"] == 3
    assert {task["title"] for task in searched["items"]} == {
        "Alpha report",
        "Beta report",
        "Delta report",
    }

    by_status = client.get("/tasks?status=pending", headers=teacher).json()
    assert by_status["total"] == 2
    assert {task["title"] for task in by_status["items"]} == {"Alpha report", "Gamma notes"}

    by_assignee = client.get(f"/tasks?assigned_to={student_id}", headers=teacher).json()
    assert by_assignee["total"] == 2
    assert {task["id"] for task in by_assignee["items"]} == set(task_ids[:2])

    combined = client.get(
        f"/tasks?search=report&status=pending&assigned_to={student_id}",
        headers=teacher,
    ).json()
    assert combined["total"] == 1
    assert combined["items"][0]["id"] == task_ids[0]

    student = bearer(login(client, "student@example.com", "student-pass-123")["access_token"])
    own_tasks = client.get("/tasks", headers=student).json()
    assert own_tasks["page"] == 1
    assert own_tasks["limit"] == 20
    assert own_tasks["total"] == 2
    assert {task["id"] for task in own_tasks["items"]} == set(task_ids[:2])
    hidden_tasks = client.get(f"/tasks?assigned_to={other_student_id}", headers=student).json()
    assert hidden_tasks["total"] == 0
    assert hidden_tasks["items"] == []


@pytest.mark.parametrize("query", ["page=0", "limit=0", "limit=101", "status=archived"])
def test_task_listing_rejects_invalid_query_parameters(client: TestClient, query: str):
    teacher = bearer(login(client, "teacher@example.com", "teacher-pass-123")["access_token"])
    assert client.get(f"/tasks?{query}", headers=teacher).status_code == 422


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
