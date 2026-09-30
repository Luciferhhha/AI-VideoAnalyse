"""ORM 模型：Video / AnalysisTask / AnalysisResult（按计划书字段）。

- 状态机：pending → running → success / failed（`TaskStatus`）。
- AnalysisTask.status 带 CHECK 约束，非法状态在数据库层也会被拒绝。
- 关系级联：删除 Video 会连带删除其 AnalysisTask 与 AnalysisResult。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


def utcnow() -> datetime:
    """当前 UTC 时间（naive，存 SQLite DATETIME 列）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TaskStatus:
    """分析任务状态机。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    ALL = frozenset({PENDING, RUNNING, SUCCESS, FAILED})


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    filepath: Mapped[str] = mapped_column(String(1024))
    duration: Mapped[float | None] = mapped_column(nullable=True)
    width: Mapped[int | None] = mapped_column(nullable=True)
    height: Mapped[int | None] = mapped_column(nullable=True)
    fps: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    tasks: Mapped[list["AnalysisTask"]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed')",
            name="ck_analysis_tasks_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    video: Mapped[Video] = relationship(back_populates="tasks")
    results: Mapped[list["AnalysisResult"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("analysis_tasks.id"))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    chapters: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    task: Mapped[AnalysisTask] = relationship(back_populates="results")
