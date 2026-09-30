"""阶段三测试：数据库建表、CRUD、状态机与约束。

- 每个测试用独立的临时 SQLite 文件库（tmp_path），不碰生产库。
- 覆盖：建表、Video CRUD、任务状态流转（含时间戳）、结果 CRUD、
  非法状态、外键约束（PRAGMA foreign_keys=ON）、级联删除。
"""

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database.database import Base, create_db_engine
from app.database.models import TaskStatus
from app.database.repository import (
    InvalidStatusError,
    ResultRepository,
    TaskRepository,
    VideoRepository,
)


@pytest.fixture()
def session(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path.as_posix()}/test.db")
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield testing_session
    testing_session.close()
    engine.dispose()


def _make_video(repo: VideoRepository, filename: str = "demo.mp4"):
    return repo.create(
        filename=filename,
        filepath=f"data/uploads/{filename}",
        duration=12.5,
        width=1920,
        height=1080,
        fps=30.0,
    )


def test_tables_created(session) -> None:
    from sqlalchemy import inspect

    names = set(inspect(session.get_bind()).get_table_names())
    assert {"videos", "analysis_tasks", "analysis_results"} <= names


def test_video_crud(session) -> None:
    repo = VideoRepository(session)

    # C
    video = _make_video(repo)
    assert video.id == 1
    assert video.filename == "demo.mp4"
    assert video.duration == 12.5
    assert video.width == 1920 and video.height == 1080
    assert video.fps == 30.0
    assert video.created_at is not None

    # R
    fetched = repo.get(video.id)
    assert fetched is not None and fetched.filepath == "data/uploads/demo.mp4"
    assert len(repo.list_all()) == 1

    # U
    updated = repo.update_info(video.id, fps=60.0, filename="renamed.mp4")
    assert updated is not None
    assert updated.fps == 60.0 and updated.filename == "renamed.mp4"
    with pytest.raises(AttributeError):
        repo.update_info(video.id, nonsense=1)

    # D
    assert repo.delete(video.id) is True
    assert repo.get(video.id) is None
    assert repo.delete(video.id) is False
    assert repo.list_all() == []


def test_task_status_lifecycle(session) -> None:
    video = VideoRepository(session).create(filename="a.mp4", filepath="data/uploads/a.mp4")
    tasks = TaskRepository(session)
    results = ResultRepository(session)

    # C：默认 pending
    task = tasks.create(video.id)
    assert task.status == TaskStatus.PENDING
    assert task.created_at is not None
    assert task.started_at is None and task.finished_at is None

    # pending → running：打 started_at
    task = tasks.update_status(task.id, TaskStatus.RUNNING)
    assert task.status == TaskStatus.RUNNING
    assert task.started_at is not None
    assert task.finished_at is None

    # running → success：打 finished_at，清 error
    task = tasks.update_status(task.id, TaskStatus.SUCCESS)
    assert task.status == TaskStatus.SUCCESS
    assert task.finished_at is not None
    assert task.error_message is None

    # R（list_by_video）
    assert len(tasks.list_by_video(video.id)) == 1

    # 结果 CRUD：写入 → 按 task 查询
    results.create(
        task.id,
        summary="一段总结",
        keywords='["ai", "video"]',
        chapters='[{"start": "00:00", "title": "开场", "summary": "..."}]',
        transcript="你好，世界",
    )
    result = results.get_by_task(task.id)
    assert result is not None
    assert result.summary == "一段总结"
    assert result.transcript == "你好，世界"
    assert result.created_at is not None
    assert len(results.list_by_task(task.id)) == 1
    assert results.delete_by_task(task.id) is True


def test_task_failed_records_error(session) -> None:
    video = VideoRepository(session).create(filename="b.mp4", filepath="data/uploads/b.mp4")
    tasks = TaskRepository(session)

    task = tasks.create(video.id)
    tasks.update_status(task.id, TaskStatus.RUNNING)
    task = tasks.update_status(task.id, TaskStatus.FAILED, error_message="ffprobe 解析失败")
    assert task.status == TaskStatus.FAILED
    assert task.error_message == "ffprobe 解析失败"
    assert task.started_at is not None and task.finished_at is not None


def test_invalid_status_rejected(session) -> None:
    video = VideoRepository(session).create(filename="c.mp4", filepath="data/uploads/c.mp4")
    tasks = TaskRepository(session)
    task = tasks.create(video.id)

    with pytest.raises(InvalidStatusError):
        tasks.update_status(task.id, "exploded")
    # 数据库里状态未被污染
    assert tasks.get(task.id).status == TaskStatus.PENDING
    # 不存在的 task 返回 None
    assert tasks.update_status(999, TaskStatus.RUNNING) is None


def test_foreign_key_enforced(session) -> None:
    tasks = TaskRepository(session)
    with pytest.raises(IntegrityError):
        tasks.create(video_id=999)  # video 不存在


def test_cascade_delete(session) -> None:
    videos = VideoRepository(session)
    tasks = TaskRepository(session)
    results = ResultRepository(session)

    video = videos.create(filename="d.mp4", filepath="data/uploads/d.mp4")
    task = tasks.create(video.id)
    results.create(task.id, summary="待清理")

    assert videos.delete(video.id) is True
    assert tasks.get(task.id) is None
    assert results.get_by_task(task.id) is None
