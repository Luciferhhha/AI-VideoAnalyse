"""视频相关 API 的出入参模型（阶段四）。"""

from datetime import datetime

from pydantic import BaseModel


class VideoCreateResponse(BaseModel):
    """POST /videos 成功响应，对应计划书示例 {"id", "filename"}。"""

    id: int
    filename: str


class VideoDetailResponse(BaseModel):
    """GET /videos/{video_id} 响应：视频信息。"""

    id: int
    filename: str
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    created_at: datetime
