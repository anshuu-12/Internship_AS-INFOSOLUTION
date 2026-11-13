from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.dependencies import CurrentUser, DbSession, TeacherUser
from app.models import Task, User
from app.schemas import (
    StudentProgress,
    TaskCreate,
    TaskPage,
    TaskProgress,
    TaskRead,
    TaskStatus,
    TaskUpdate,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])
STATUSES = ("pending", "in progress", "done")


def require_student_assignee(db: DbSession, user_id: str | None) -> None:
    if user_id is None:
        return
    assignee = db.get(User, user_id)
    if assignee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    if assignee.role != "student":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tasks can only be assigned to students",
        )


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, db: DbSession, teacher: TeacherUser) -> Task:
    require_student_assignee(db, payload.assigned_to)
    task = Task(
        title=payload.title.strip(),
        description=payload.description,
        assigned_to=payload.assigned_to,
        created_by=teacher.id,
    )
    if not task.title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Title cannot be blank")
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/progress", response_model=TaskProgress)
def task_progress(db: DbSession, _: TeacherUser) -> TaskProgress:
    total = db.scalar(select(func.count(Task.id))) or 0
    counts = [
        db.scalar(select(func.count(Task.id)).where(Task.status == task_status)) or 0
        for task_status in STATUSES
    ]
    students = db.scalars(
        select(User).where(User.role == "student").order_by(User.full_name, User.email)
    ).all()
    student_rows: list[StudentProgress] = []
    for student in students:
        rows = db.execute(
            select(Task.status, func.count(Task.id))
            .where(Task.assigned_to == student.id)
            .group_by(Task.status)
        ).all()
        by_status = dict(rows)
        student_rows.append(
            StudentProgress(
                student_id=student.id,
                student_name=student.full_name,
                total=sum(by_status.values()),
                pending=by_status.get("pending", 0),
                in_progress=by_status.get("in progress", 0),
                done=by_status.get("done", 0),
            )
        )
    return TaskProgress(
        total=total,
        pending=counts[0],
        in_progress=counts[1],
        done=counts[2],
        students=student_rows,
    )


@router.get("", response_model=TaskPage)
def list_tasks(
    db: DbSession,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=200),
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    assigned_to: str | None = None,
) -> TaskPage:
    filters = []
    if user.role == "student":
        filters.append(Task.assigned_to == user.id)
    if search is not None and search.strip():
        filters.append(Task.title.icontains(search.strip(), autoescape=True))
    if status_filter is not None:
        filters.append(Task.status == status_filter)
    if assigned_to is not None:
        filters.append(Task.assigned_to == assigned_to)

    total = db.scalar(select(func.count(Task.id)).where(*filters)) or 0
    query = (
        select(Task)
        .where(*filters)
        .order_by(Task.created_at.desc(), Task.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return TaskPage(
        items=list(db.scalars(query).all()),
        page=page,
        limit=limit,
        total=total,
    )


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: str, db: DbSession, user: CurrentUser) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if user.role == "student" and task.assigned_to != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(task_id: str, payload: TaskUpdate, db: DbSession, user: CurrentUser) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if user.role == "student":
        if task.assigned_to != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        forbidden_fields = payload.model_fields_set - {"status", "work_notes"}
        if forbidden_fields:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Students may only update task status and work notes",
            )
    else:
        if "assigned_to" in payload.model_fields_set:
            require_student_assignee(db, payload.assigned_to)

    for field in payload.model_fields_set:
        setattr(task, field, getattr(payload, field))
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: str, db: DbSession, _: TeacherUser) -> Response:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    db.delete(task)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
