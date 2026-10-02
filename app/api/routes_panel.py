"""控制面板端点（阶段十六）：日志流 `GET /logs`、配置快照 `GET /settings` 与
API Key 管理 `/api-keys/mimo`。

只读端点无副作用；Key 管理端点只改本机密钥文件与运行时 config，不联网。
页面本身由 `app/main.py` 的 `GET /` 提供。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.schemas.panel import (
    ApiKeyStatusResponse,
    ApiKeyUpdateRequest,
    LogsResponse,
    SettingsResponse,
)
from app.services import key_service, log_service, panel_service

router = APIRouter(tags=["panel"])

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


@router.get("/logs", response_model=LogsResponse)
def get_logs(
    limit: int = Query(default=200, ge=1, le=1000, description="返回条数（旧→新）"),
    level: LogLevel | None = Query(
        default=None,
        description="只保留该级别及以上（DEBUG/INFO/WARNING/ERROR/CRITICAL）",
    ),
) -> LogsResponse:
    """最近日志（进程内环形缓冲，进程重启即清空）。"""
    return LogsResponse(
        items=log_service.recent(limit=limit, level=level),
        total=log_service.total(),
    )


@router.get("/settings", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    """当前运行配置快照（只回传是否已配置 Key + 掩码，不含 Key 明文）。"""
    return SettingsResponse(**panel_service.settings_snapshot())


# ---------------------------------------------------------------- API Key 管理


@router.get("/api-keys/mimo", response_model=ApiKeyStatusResponse)
def get_api_key_status() -> ApiKeyStatusResponse:
    """mimo API Key 状态：是否已配置、掩码、来源与密钥文件信息（无明文）。"""
    return ApiKeyStatusResponse(**key_service.status())


@router.put("/api-keys/mimo", response_model=ApiKeyStatusResponse)
def put_api_key(payload: ApiKeyUpdateRequest) -> ApiKeyStatusResponse:
    """新增/更换 API Key：DPAPI 加密落盘并立即生效（无需重启）。"""
    try:
        return ApiKeyStatusResponse(**key_service.save(payload.api_key))
    except key_service.KeyValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except key_service.KeyServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/api-keys/mimo", response_model=ApiKeyStatusResponse)
def delete_api_key() -> ApiKeyStatusResponse:
    """清空/删除 API Key：删除密钥文件，回落到环境变量 `MIMO_API_KEY`。"""
    try:
        return ApiKeyStatusResponse(**key_service.clear())
    except key_service.KeyServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
