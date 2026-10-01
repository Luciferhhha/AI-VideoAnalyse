"""异步分析任务的后台执行（阶段五）。

流程（计划书第八章）：
`POST /videos/{video_id}/analyze` 创建任务并立即返回 task_id，随后由 FastAPI
`BackgroundTasks` 在响应发送后于线程池中调用 `run_analysis_task`：
pending → running → success / failed。

约定：
- 后台异常一律被捕获并写入 `task.error_message`，服务不得崩溃（5.3）。
- 阶段九起为完整链路（9.4）：音频提取（六）→ 语音转写（七）→ 关键帧
  提取（八）→ AI 分析（九）→ 写入 `AnalysisResult`。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import config
from app.database.models import TaskStatus
from app.database.repository import ResultRepository, TaskRepository, VideoRepository
from app.services.analysis_service import (
    generate_chapters,
    generate_keywords,
    generate_summary,
)
from app.services.audio_service import extract_audio
from app.services.keyframe_service import extract_keyframes
from app.services.transcription_service import get_transcription_service

logger = logging.getLogger(__name__)


class AnalysisError(Exception):
    """分析流程中可预期的失败（原因写入 task.error_message）。"""


def analyze_video(
    _session: Session, video, *, task_id: int | None = None
) -> dict[str, str | None]:
    """完整分析链路（9.4）：校验视频 → 分析（10.6 两种驱动）→ 写库字段。

    - `config.AGENT_DRIVER == "agent"`（默认）：由 Agent 循环驱动 8 个 Tool
      完成分析；save_result Tool 直接写入 `AnalysisResult`。
    - `config.AGENT_DRIVER == "direct"`：阶段九直连链路（对照/降级）。

    失败（9.2 非法 JSON、未配置 API Key、Tool 失败、超限等）抛
    `AnalysisError`/`AgentError`，由 `run_analysis_task` 落
    `task.error_message` 并标记任务 failed，程序不崩溃。
    """
    if not Path(video.filepath).is_file():
        raise AnalysisError(f"视频文件不存在：{video.filepath}")
    driver = (config.AGENT_DRIVER or "agent").strip().lower()
    if driver == "direct":
        return _direct_analyze(video)
    if driver == "agent":
        return _agent_analyze(_session, video, task_id)
    raise AnalysisError(f"未知的分析驱动方式：{config.AGENT_DRIVER}（可选 agent / direct）")


def _direct_analyze(video) -> dict[str, str | None]:
    """阶段九直连链路：音频提取 → 语音转写 → 关键帧提取 → AI 分析。"""
    audio_path = extract_audio(video.filepath, video.id)
    transcript = get_transcription_service().transcribe(audio_path)["text"]
    keyframe_paths = [
        rec["filepath"]
        for rec in extract_keyframes(video.filepath, video.id)
    ]
    summary = generate_summary(transcript, keyframes=keyframe_paths)
    keywords = generate_keywords(transcript, keyframes=keyframe_paths)
    chapters = generate_chapters(
        transcript, duration=video.duration, keyframes=keyframe_paths
    )
    return {
        "summary": summary,
        # Text 列存 JSON 字符串（关键词数组 / 9.3 章节格式）
        "keywords": json.dumps(keywords, ensure_ascii=False),
        "chapters": json.dumps(chapters, ensure_ascii=False),
        "transcript": transcript,
    }


def _agent_analyze(
    session: Session, video, task_id: int | None
) -> dict[str, str | None]:
    """阶段十（10.6）：Agent 驱动分析 —— LLM 循环调用 8 个 Tool 完成链路。

    - save_result Tool 已写入 `AnalysisResult`（run_analysis_task 幂等跳过重复写）。
    - Agent 任一 Tool 失败（state.errors 非空）即任务失败并记录首个原因。
    """
    from app.agent.agent import AgentContext, run_agent
    from app.agent.tools import ToolError, build_result_fields

    state = run_agent(AgentContext(video=video, session=session, task_id=task_id))
    if state.errors:
        raise AnalysisError(f"Agent 分析失败：{state.errors[0]}")
    try:
        return build_result_fields(state)
    except ToolError as exc:
        raise AnalysisError(str(exc)) from exc


def run_analysis_task(task_id: int, engine: Engine) -> None:
    """后台执行一个分析任务；任何异常只写库与日志，不向外抛出。"""
    session: Session | None = None
    try:
        # 独立 Session（与请求同库），后台线程不复用请求会话
        session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
        tasks = TaskRepository(session)
        task = tasks.get(task_id)
        if task is None:
            logger.warning("后台任务跳过：任务不存在 task_id=%s", task_id)
            return

        tasks.update_status(task_id, TaskStatus.RUNNING)
        logger.info("任务启动 task_id=%s video_id=%s", task_id, task.video_id)

        video = VideoRepository(session).get(task.video_id)
        if video is None:
            raise AnalysisError(f"关联视频不存在：id={task.video_id}")

        # task_id 传给 analyze_video：Agent 驱动下 save_result Tool 需要它
        result = analyze_video(session, video, task_id=task_id)
        # Agent 驱动下 save_result Tool 已写入结果 → 幂等跳过，避免重复插入
        if ResultRepository(session).get_by_task(task_id) is None:
            ResultRepository(session).create(task_id, **result)

        tasks.update_status(task_id, TaskStatus.SUCCESS)
        logger.info("任务完成 task_id=%s status=success", task_id)
    except Exception as exc:  # noqa: BLE001 —— 后台任务必须自愈，服务不得崩溃
        message = str(exc) or exc.__class__.__name__
        logger.exception("任务失败 task_id=%s：%s", task_id, message)
        if session is None:
            return
        try:
            session.rollback()
            TaskRepository(session).update_status(
                task_id, TaskStatus.FAILED, error_message=message
            )
        except Exception:  # noqa: BLE001 —— 连失败状态都写不进去时只记日志
            logger.exception("任务失败状态写入未成功 task_id=%s", task_id)
    finally:
        if session is not None:
            session.close()
