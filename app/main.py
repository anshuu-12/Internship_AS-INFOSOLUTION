from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import os

from fastapi import FastAPI

from app.database import Base, engine
from app import models
from app.routers import auth, tasks, users


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Task Manager API",
    description="PostgreSQL-backed task management with teacher/student roles and JWT sessions.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(tasks.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "environment": os.getenv("APP_ENV", "development")}
