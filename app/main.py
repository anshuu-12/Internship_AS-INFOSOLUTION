import os

from fastapi import FastAPI

from app.routers import auth, tasks, users


app = FastAPI(
    title="Task Manager API",
    description="PostgreSQL-backed task management with teacher/student roles and JWT sessions.",
    version="1.0.0",
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(tasks.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "environment": os.getenv("APP_ENV", "development")}
