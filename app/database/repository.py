"""Repository 层：所有数据库读写集中于此。

规则（计划书要求）：API 路由禁止直接写 SQL / 操作 ORM 查询，
统一通过本模块的 Repository 类访问数据。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import (
    AnalysisResult,
    AnalysisTask,
    TaskStatus,
    Video,
    utcnow,
)


class RepositoryError(Exception):
    """Repository 层错误基类。"""


class InvalidStatusError(RepositoryError):
    """状态机非法状态或非法流转。"""


class VideoRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        filename: str,
        filepath: str,
        duration: float | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
    ) -> Video:
        video = Video(
            filename=filename,
            filepath=filepath,
            duration=duration,
            width=width,
            height=height,
            fps=fps,
        )
        self.session.add(video)
        self.session.commit()
        return video

    def get(self, video_id: int) -> Video | None:
        return self.session.get(Video, video_id)

    def list_all(self) -> list[Video]:
        return list(self.session.scalars(select(Video).order_by(Video.id)))

    def update_info(self, video_id: int, **fields) -> Video | None:
        """更新视频元数据字段；未知字段抛 AttributeError。"""
        video = self.get(video_id)
        if video is None:
            return None
        allowed = {"filename", "filepath", "duration", "width", "height", "fps"}
        unknown = set(fields) - allowed
        if unknown:
            raise AttributeError(f"Video 不支持的字段：{sorted(unknown)}")
        for key, value in fields.items():
            setattr(video, key, value)
        self.session.commit()
        return video

    def delete(self, video_id: int) -> bool:
        """删除视频（级联删除其任务与结果）；不存在返回 False。"""
        video = self.get(video_id)
        if video is None:
            return False
        self.session.delete(video)
        self.session.commit()
        return True


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, video_id: int) -> AnalysisTask:
        task = AnalysisTask(video_id=video_id, status=TaskStatus.PENDING)
        self.session.add(task)
        self.session.commit()
        return task

    def get(self, task_id: int) -> AnalysisTask | None:
        return self.session.get(AnalysisTask, task_id)

    def list_by_video(self, video_id: int) -> list[AnalysisTask]:
        stmt = (
            select(AnalysisTask)
            .where(AnalysisTask.video_id == video_id)
            .order_by(AnalysisTask.id)
        )
        return list(self.session.scalars(stmt))

    def list_all(self) -> list[AnalysisTask]:
        """全部任务，按 id 升序（控制面板列表由调用方自行倒序）。"""
        return list(self.session.scalars(select(AnalysisTask).order_by(AnalysisTask.id)))

    def update_status(
        self,
        task_id: int,
        status: str,
        *,
        error_message: str | None = None,
    ) -> AnalysisTask | None:
        """更新状态并自动打时间戳：running→started_at，success/failed→finished_at。"""
        if status not in TaskStatus.ALL:
            raise InvalidStatusError(
                f"非法任务状态：{status!r}，允许值：{sorted(TaskStatus.ALL)}"
            )
        task = self.get(task_id)
        if task is None:
            return None
        task.status = status
        if status == TaskStatus.RUNNING and task.started_at is None:
            task.started_at = utcnow()
        if status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            task.finished_at = utcnow()
            task.error_message = error_message if status == TaskStatus.FAILED else None
        elif status == TaskStatus.PENDING:
            # 允许重置回 pending（重新排队），清空时间戳
            task.started_at = None
            task.finished_at = None
            task.error_message = None
        self.session.commit()
        return task

    def delete(self, task_id: int) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        self.session.delete(task)
        self.session.commit()
        return True


class ResultRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        task_id: int,
        *,
        summary: str | None = None,
        keywords: str | None = None,
        chapters: str | None = None,
        transcript: str | None = None,
    ) -> AnalysisResult:
        result = AnalysisResult(
            task_id=task_id,
            summary=summary,
            keywords=keywords,
            chapters=chapters,
            transcript=transcript,
        )
        self.session.add(result)
        self.session.commit()
        return result

    def get_by_task(self, task_id: int) -> AnalysisResult | None:
        stmt = (
            select(AnalysisResult)
            .where(AnalysisResult.task_id == task_id)
            .order_by(AnalysisResult.id.desc())
        )
        return self.session.scalars(stmt).first()

    def list_by_task(self, task_id: int) -> list[AnalysisResult]:
        stmt = (
            select(AnalysisResult)
            .where(AnalysisResult.task_id == task_id)
            .order_by(AnalysisResult.id)
        )
        return list(self.session.scalars(stmt))

    def delete_by_task(self, task_id: int) -> bool:
        results = self.list_by_task(task_id)
        if not results:
            return False
        for result in results:
            self.session.delete(result)
        self.session.commit()
        return True
