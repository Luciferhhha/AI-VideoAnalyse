"""控制面板专用 API 出入参（阶段十六）：日志流与运行配置快照。"""

from __future__ import annotations

from pydantic import BaseModel


class LogLineResponse(BaseModel):
    """一条进程内日志（内存环形缓冲，见 `app/services/log_service.py`）。"""

    ts: str  # "YYYY-MM-DD HH:MM:SS"
    level: str  # INFO / WARNING / ERROR ...
    logger: str  # 记录器名，如 app.services.task_service
    message: str


class LogsResponse(BaseModel):
    """GET /logs 响应：items 为旧→新的日志行，total 为缓冲内总条数。"""

    items: list[LogLineResponse]
    total: int


class AppInfo(BaseModel):
    title: str
    version: str


class ProvidersInfo(BaseModel):
    """三处 provider + 分析驱动方式（读取请求时刻的 config，支持测试改写）。"""

    transcription: str
    analysis: str
    agent: str
    driver: str


class ModelsInfo(BaseModel):
    asr: str
    analysis: str
    agent: str


class MimoInfo(BaseModel):
    base_url: str
    api_key_configured: bool  # 只暴露是否已配置，绝不回传 Key 本身


class LimitsInfo(BaseModel):
    max_upload_size_mb: int
    allowed_extensions: list[str]


class SettingsResponse(BaseModel):
    """GET /settings 响应：面板「API 控制」区的配置只读快照。"""

    app: AppInfo
    providers: ProvidersInfo
    models: ModelsInfo
    mimo: MimoInfo
    limits: LimitsInfo
    keyframe_interval_seconds_default: float
    ffmpeg_available: bool
    ffprobe_available: bool
