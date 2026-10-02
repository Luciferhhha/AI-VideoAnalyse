"""进程内日志环形缓冲：给控制面板「日志流程」区提供最近日志（阶段十六）。

约定：
- 只在内存保留最近 `CAPACITY` 条（默认 1000），进程重启即清空；
- `install()` 幂等：重复调用不重复挂 handler，测试里多次启动 lifespan 也安全；
- 端点 `GET /logs` 只读本缓冲，不落盘、不阻塞；handler 自身异常不外抛。
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import Any

CAPACITY = 1000
_HANDLER_NAME = "panel-memory"

# 模块级缓冲：install() 与 recent() 共享同一份数据
_buffer: deque[dict[str, Any]] = deque(maxlen=CAPACITY)
_installed = False


class MemoryLogHandler(logging.Handler):
    """把记录以 dict 形式追加进模块缓冲（INFO 及以上）。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            message = self.format(record)
            if record.exc_info:
                message = f"{message} | {self.formatException(record.exc_info)}"
            _buffer.append(
                {
                    "ts": datetime.fromtimestamp(record.created).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": message,
                }
            )
        except Exception:  # noqa: BLE001 —— 日志记录本身绝不能让业务崩溃
            self.handleError(record)


def install() -> None:
    """把环形缓冲 handler 挂到 root logger（幂等）。"""
    global _installed
    if _installed:
        return
    root = logging.getLogger()
    # 保证 INFO 能到达缓冲：面板靠 INFO 级的任务流程日志工作，
    # 而测试/宿主进程可能已把 root 级别抬到 WARNING（basicConfig 会因此失效）。
    if root.getEffectiveLevel() > logging.INFO:
        root.setLevel(logging.INFO)
    for handler in root.handlers:
        if handler.get_name() == _HANDLER_NAME:
            _installed = True
            return
    handler = MemoryLogHandler()
    handler.set_name(_HANDLER_NAME)
    root.addHandler(handler)
    _installed = True


def recent(limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
    """返回最近 `limit` 条日志（旧→新）；`level` 给定时只保留该级别及以上。"""
    items: list[dict[str, Any]] = list(_buffer)
    if level:
        keep = _level_value(level)
        items = [item for item in items if _level_value(item["level"]) >= keep]
    if limit >= 0:
        items = items[-limit:]
    return items


def _level_value(name: str) -> int:
    value = logging.getLevelName(name.upper())
    return value if isinstance(value, int) else logging.INFO


def total() -> int:
    """缓冲内当前条数。"""
    return len(_buffer)


def clear() -> None:
    """清空缓冲（测试与面板「清空日志」用）。"""
    _buffer.clear()
