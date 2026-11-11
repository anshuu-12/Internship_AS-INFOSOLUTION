# Task Manager API

A FastAPI task manager backed by PostgreSQL. It provides teacher and student roles, hashed passwords, signed JWT access and refresh tokens, and revocable server-side sessions.

## Requirements

- Python 3.11 or newer
- PostgreSQL 14 or newer, locally or hosted (for example, Supabase)

## Install

From the project directory, create and activate a virtual environment, then install dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set your database URL and a private random JWT secret of at least 32 characters. For example, generate a secret in PowerShell with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the generated value in your local `.env`. `.env` is ignored by Git and must not be committed.

## PostgreSQL setup

Create a PostgreSQL database and user locally, then set `DATABASE_URL` in `.env`:

```text
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/task_manager
```

URL-encode special characters in the username or password. If local PostgreSQL is unavailable, create a Supabase PostgreSQL project and use its connection string in the same SQLAlchemy form, including `?sslmode=require` when required by the provider:

```text
DATABASE_URL=postgresql+psycopg://postgres:YOUR_DATABASE_PASSWORD@db.YOUR_PROJECT.supabase.co:5432/postgres?sslmode=require
```

On startup, SQLAlchemy creates missing tables. The API does not run schema migrations; back up the database before making schema changes to a deployed instance.

### Docker Compose

The repository's Compose setup starts both the API and a PostgreSQL container with a persistent data volume. Set the database and JWT secrets in `.env`, then run:

```powershell
docker compose up --build
```

Compose uses `DATABASE_URL_DOCKER` to connect the API to its `db` service. To seed accounts in that database, run:

```powershell
docker compose run --rm -e TEACHER_EMAIL -e TEACHER_NAME -e TEACHER_PASSWORD -e STUDENT_EMAIL -e STUDENT_NAME -e STUDENT_PASSWORD api python -m scripts.seed_users
```

Set those six seed values in the shell before running the command. Do not commit `.env` or use real credentials in the repository.

## Seed teacher and student accounts

Set the following environment variables in the local shell before running the seed script. Use unique email addresses and passwords of at least 8 characters. Do not put real passwords in this README, source code, or a committed file.

```powershell
$env:TEACHER_EMAIL = "teacher@example.com"
$env:TEACHER_NAME = "Course Teacher"
$env:TEACHER_PASSWORD = "<set-a-private-password-here>"
$env:STUDENT_EMAIL = "student@example.com"
$env:STUDENT_NAME = "Example Student"
$env:STUDENT_PASSWORD = "<set-a-different-private-password-here>"
python -m scripts.seed_users
```

The script hashes passwords before storing them. It refuses to overwrite a user if either email already exists. Public registration (`POST /auth/register`) creates student accounts only; it cannot grant teacher privileges.

## Start the API

With `.env` configured and the virtual environment active:

```powershell
uvicorn app.main:app --reload
```

- API: <http://127.0.0.1:8000>
- Interactive OpenAPI docs: <http://127.0.0.1:8000/docs>
- Health check: `GET /health`

Run automated tests with:

```powershell
python -m pytest
```

Tests use an isolated in-memory SQLite database. Running the application itself requires PostgreSQL.

## Authentication and sessions

1. `POST /auth/login` with JSON `{"email":"...","password":"..."}` returns an access token and a refresh token.
2. Send the access token on protected routes as `Authorization: Bearer <access_token>`. Access tokens expire after `ACCESS_TOKEN_MINUTES` (default 15).
3. `POST /auth/refresh` with `{"refresh_token":"..."}` rotates the refresh token and returns a new token pair. The previous refresh token cannot be reused.
4. `POST /auth/logout` with the current `{"refresh_token":"..."}` revokes its server-side session. Both access and refresh tokens for that session are then rejected.
5. Refresh sessions expire after `REFRESH_TOKEN_DAYS` (default 7). The database stores a SHA-256 hash of each refresh token, not the token itself. Passwords are hashed with Argon2.
6. `GET /auth/me` returns the authenticated user's profile.

An unauthenticated, invalid, expired, or revoked token returns **401 Unauthorized**. A logged-in user who lacks the required role or action permission receives **403 Forbidden**. A missing resource is **404 Not Found**, and invalid request data is **422 Unprocessable Entity**.

## Roles and routes

| Route | Teacher | Student |
|---|---|---|
| `POST /auth/register` | Public registration creates students only | Public registration creates students only |
| `POST /auth/login`, `/auth/refresh`, `/auth/logout` | Yes | Yes |
| `GET /auth/me`, `GET /users/me` | Own profile | Own profile |
| `GET /users` | View all users | 403 |
| `POST /tasks` | Create and optionally assign to a student | 403 |
| `GET /tasks`, `GET /tasks/{id}` | View all tasks | View only tasks assigned to them |
| `PATCH /tasks/{id}` | Update task fields and assign/unassign | Update only own task `status` and `work_notes` |
| `DELETE /tasks/{id}` | Delete task | 403 |
| `GET /tasks/progress` | View all task counts and student progress | 403 |

Task statuses are `pending`, `in progress`, and `done`. Assigning a task to a nonexistent student returns 404; assigning to a teacher returns 422.

## Postman verification

Set a collection variable `baseUrl` to `http://127.0.0.1:8000`. For each login, save `access_token` and `refresh_token` from the response; use `Authorization` → `Bearer Token` for protected requests.

1. Log in as the seeded teacher at `POST {{baseUrl}}/auth/login`.
2. Log in as the seeded student at `POST {{baseUrl}}/auth/login`.
3. As the teacher, call `GET {{baseUrl}}/users`, copy the student's `id`, then `POST {{baseUrl}}/tasks` with `{"title":"Read chapter 1","assigned_to":"<student-id>"}`.
4. As the student, call `GET {{baseUrl}}/tasks`, then `PATCH {{baseUrl}}/tasks/<task-id>` with `{"status":"in progress","work_notes":"Started reading"}`.
5. Confirm student `POST /tasks` and `DELETE /tasks/<task-id>` return 403. Also confirm a student cannot access `GET /users` or `GET /tasks/progress`.
6. Try an incorrect password (401), a protected request without a token (401), a malformed task body (422), and an unknown task ID (404).
7. Call `POST /auth/refresh` with the current refresh token, then try the old refresh token again (401). Call `POST /auth/logout` with the newly issued refresh token; the associated access token and refresh token must both be rejected afterward (401).
8. For an expired-token check, generate a locally signed, already-expired access token (the command reads the secret from `.env` without printing it), paste the result as a Postman bearer token, then call `GET {{baseUrl}}/auth/me`:

   ```powershell
   python -c "import datetime, jwt; from app.config import get_settings; now=datetime.datetime.now(datetime.timezone.utc); print(jwt.encode({'sub':'expired-check','sid':'expired-check','type':'access','iat':now-datetime.timedelta(minutes=2),'exp':now-datetime.timedelta(minutes=1)}, get_settings().jwt_secret.get_secret_value(), algorithm='HS256'))"
   ```

   The request must return 401. Never expose the secret outside the local environment.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | No | Local PostgreSQL URL | PostgreSQL connection string using the Psycopg 3 driver |
| `JWT_SECRET` | Yes | — | Private signing secret, at least 32 characters |
| `ACCESS_TOKEN_MINUTES` | No | `15` | Access token lifetime in minutes |
| `REFRESH_TOKEN_DAYS` | No | `7` | Refresh session lifetime in days |
