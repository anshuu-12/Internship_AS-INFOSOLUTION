from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


Role = Literal["teacher", "student"]
TaskStatus = Literal["pending", "in progress", "done"]


class BaseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserRegister(BaseSchema):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Full name cannot be blank")
        return normalized


class UserRead(BaseSchema):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: str
    role: Role
    created_at: datetime


class LoginRequest(BaseSchema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseSchema):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseSchema):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    access_expires_in: int


class TaskCreate(BaseSchema):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    assigned_to: str | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Title cannot be blank")
        return normalized


class TaskUpdate(BaseSchema):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    status: TaskStatus | None = None
    work_notes: str | None = Field(default=None, max_length=10000)
    assigned_to: str | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("Title cannot be blank")
        return normalized

    @model_validator(mode="after")
    def require_update_field(self) -> "TaskUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        for field in ("title", "description", "status", "work_notes", "assigned_to"):
            if field in self.model_fields_set and getattr(self, field) is None and field != "assigned_to":
                raise ValueError(f"{field} cannot be null")
        return self


class TaskRead(BaseSchema):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str
    status: TaskStatus
    work_notes: str
    assigned_to: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class StudentProgress(BaseSchema):
    student_id: str
    student_name: str
    total: int
    pending: int
    in_progress: int
    done: int


class TaskProgress(BaseSchema):
    total: int
    pending: int
    in_progress: int
    done: int
    students: list[StudentProgress]
