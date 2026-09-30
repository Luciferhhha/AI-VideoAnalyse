"""异步分析任务 API（阶段五）。

- POST /videos/{video_id}/analyze：创建 task → 立即返回 task_id → 后台执行。
- GET /tasks/{task_id}：返回状态，失败时带 error_message。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.repository import TaskRepository, VideoRepository
from app.schemas.task import TaskCreateResponse, TaskDetailResponse
from app.services.task_service import run_analysis_task

router = APIRouter(tags=["tasks"])


def _detail_response(task) -> TaskDetailResponse:
    return TaskDetailResponse(
        task_id=task.id,
        video_id=task.video_id,
        status=task.status,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        error_message=task.error_message,
    )


@router.post(
    "/videos/{video_id}/analyze",
    response_model=TaskCreateResponse,
    status_code=202,
)
def create_analyze_task(
    video_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> TaskCreateResponse:
    # 1) 视频必须存在，否则 404
    if VideoRepository(db).get(video_id) is None:
        raise HTTPException(status_code=404, detail=f"视频不存在：id={video_id}")

    # 2) 创建 task（pending）并立即返回 task_id
    task = TaskRepository(db).create(video_id)

    # 3) 响应发出后由线程池执行：后台用与请求同库的独立 Session
    background_tasks.add_task(run_analysis_task, task.id, db.get_bind())

    return TaskCreateResponse(task_id=task.id, video_id=task.video_id, status=task.status)


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskDetailResponse:
    task = TaskRepository(db).get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：id={task_id}")
    return _detail_response(task)
