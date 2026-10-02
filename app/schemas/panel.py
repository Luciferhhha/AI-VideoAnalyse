"""控制面板专用 API 出入参（阶段十六）：日志流与运行配置快照。"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    api_key_masked: str | None = None  # 形如 sk-abcd****wxyz（永不回传明文）
    api_key_source: str = "none"  # file / env / config / none


class ApiKeyStatusResponse(BaseModel):
    """`/api-keys/mimo` 的响应：掩码与来源，**永不包含明文**。"""

    configured: bool
    masked: str | None = None
    source: str  # file（面板保存的密钥文件）/ env（环境变量）/ config / none
    storage: str  # 恒为 dpapi（Windows 用户级静态加密）
    storage_path: str  # 密钥文件相对路径，如 data/secrets/mimo_api_key.bin
    updated_at: str | None = None  # 密钥文件修改时间，未保存过为 None
    error: str | None = None  # 最近一次读取失败原因（解密失败等）


class ApiKeyUpdateRequest(BaseModel):
    """`PUT /api-keys/mimo` 请求体：要保存的新 API Key。"""

    api_key: str = Field(
        min_length=1,
        max_length=512,
        description="完整 mimo API Key（服务端会去掉首尾空白与成对引号）",
    )


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
