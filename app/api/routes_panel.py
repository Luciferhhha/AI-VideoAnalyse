"""控制面板端点（阶段十六）：日志流 `GET /logs` 与配置快照 `GET /settings`。

只读、无副作用；页面本身由 `app/main.py` 的 `GET /` 提供。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app.schemas.panel import LogsResponse, SettingsResponse
from app.services import log_service, panel_service

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
    """当前运行配置快照（只回传是否已配置 Key，不含 Key 明文）。"""
    return SettingsResponse(**panel_service.settings_snapshot())
