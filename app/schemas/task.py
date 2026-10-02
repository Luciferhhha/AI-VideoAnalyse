"""分析任务相关 API 的出入参模型（阶段五）。"""

from datetime import datetime

from pydantic import BaseModel


class TaskCreateResponse(BaseModel):
    """POST /videos/{video_id}/analyze 响应：创建后立即返回 task_id。"""

    task_id: int
    video_id: int
    status: str


class TaskDetailResponse(BaseModel):
    """GET /tasks/{task_id} 响应：状态流转与失败原因。"""

    task_id: int
    video_id: int
    status: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None


class TaskResultResponse(BaseModel):
    """GET /tasks/{task_id}/result 响应：分析产物（库中 JSON 字符串在此还原为结构）。"""

    task_id: int
    video_id: int
    status: str
    summary: str | None = None
    keywords: list[str] = []
    chapters: list[dict] = []
    transcript: str | None = None
    finished_at: datetime | None = None


class TaskListResponse(BaseModel):
    """GET /tasks 响应：控制面板用的任务列表行（新→旧，带视频文件名）。"""

    task_id: int
    video_id: int
    video_filename: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
