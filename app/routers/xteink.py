from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import BRIEFING_DIR, LIBRARY_DIR
from app.services.library import media_type_for
from app.db import get_db
from app.models import SyncTask, utcnow
from app.services import hostname, settings

router = APIRouter()


class TaskCompletion(BaseModel):
    status: str


class TaskCreate(BaseModel):
    device_id: str
    file_url: str
    save_path: str
    size: int | None = None


def _task_dict(task: SyncTask, base_url: str) -> dict:
    created = int(task.created_at.timestamp()) if task.created_at else int(datetime.now(timezone.utc).timestamp())
    return {
        "task_id": task.task_id,
        "device_id": task.device_id,
        "status": task.status,
        "file_url": f"{base_url.rstrip('/')}/api/v1/files/{Path(task.file_path).name}",
        "save_path": task.save_path,
        "size": task.size,
        "created_at": created,
        "expires_at": created + 60 * 60 * 24 * 3,
        "completed_at": int(task.completed_at.timestamp()) if task.completed_at else None,
    }


@router.get("/api/v1/health")
def health():
    return {"message": "NewsCast reader sync", "status": "ok"}


@router.post("/auth/refresh")
def auth_refresh():
    return {"access_token": "newscast-local", "success": True}


@router.get("/api/v1/device/tasks")
def list_tasks(
    db: Annotated[Session, Depends(get_db)],
    device_id: str = "",
    status: str = "all",
    limit: int | None = 4,
):
    query = db.query(SyncTask)
    expected_device = settings.get_value(db, "x3_device_id")
    if device_id:
        query = query.filter((SyncTask.device_id == device_id) | (SyncTask.device_id == "") | (SyncTask.device_id == expected_device))
    if status and status != "all":
        query = query.filter(SyncTask.status == status)
    order = {"pending": 0, "processing": 1, "failed": 2, "completed": 3}
    tasks = query.order_by(SyncTask.created_at.desc()).all()
    tasks.sort(key=lambda task: order.get(task.status, 9))
    if limit:
        tasks = tasks[:limit]
    pending = sum(1 for task in tasks if task.status == "pending")
    processing = sum(1 for task in tasks if task.status == "processing")
    done = sum(1 for task in tasks if task.status == "completed")
    return {
        "success": True,
        "tasks": [_task_dict(task, hostname.get_public_base_url(db)) for task in tasks],
        "total": len(tasks),
        "total_done": done,
        "total_pending": pending,
        "total_processing": processing,
        "code": 0,
    }


@router.post("/api/v1/device/tasks")
def create_task(payload: TaskCreate, db: Annotated[Session, Depends(get_db)]):
    raise HTTPException(status_code=400, detail="Create tasks by refreshing NewsCast instead.")


@router.post("/api/v1/device/tasks/{task_id}/complete")
def complete_task(task_id: str, payload: TaskCompletion, db: Annotated[Session, Depends(get_db)]):
    task = db.query(SyncTask).filter(SyncTask.task_id == task_id).one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = payload.status if payload.status in {"completed", "failed", "pending"} else "completed"
    if task.status == "completed":
        task.completed_at = utcnow()
    db.commit()
    return {"success": True}


def _resolve_push_file(filename: str) -> Path | None:
    safe = Path(filename).name
    for folder in (LIBRARY_DIR, BRIEFING_DIR):
        path = folder / safe
        if path.exists():
            return path
    return None


@router.get("/api/v1/files/{filename}")
def download_file(filename: str):
    path = _resolve_push_file(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type=media_type_for(path), filename=path.name)
