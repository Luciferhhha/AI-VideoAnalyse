"""Agent Tool 层（计划书 10.1）：8 个 Tool，注册表 + 统一执行入口。

约定：
- 每个 Tool 有明确输入（JSON Schema，供 LLM function calling）与明确输出
  （可 JSON 序列化的 dict，作为 tool 消息回传 LLM，并统一记入
  `AgentState.intermediate_results[tool_name]`）。
- 异常统一转为 `ToolError`：由 Agent 循环捕获、记入 `AgentState.errors`
  并以 `{"error": …}` 回传给 LLM，程序不崩溃。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.agent import prompts
from app.database.repository import ResultRepository
from app.services.analysis_service import (
    generate_chapters,
    generate_keywords,
    generate_summary,
)
from app.services.audio_service import extract_audio as _extract_audio
from app.services.keyframe_service import extract_keyframes as _extract_keyframes
from app.services.transcription_service import get_transcription_service
from app.services.video_service import get_video_info as _get_video_info


class ToolError(Exception):
    """Tool 执行失败（原因回传 LLM 并记入 AgentState.errors）。"""


@dataclass(frozen=True)
class ToolSpec:
    """一个 Tool 的完整定义：名称、输入 Schema、处理函数。"""

    name: str
    parameters: dict[str, Any]
    handler: Callable[["AgentContext", "AgentState", dict[str, Any]], dict[str, Any]]


# 上下文/状态在 agent.py 中定义；这里仅作类型前向引用（字符串形式）
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.agent import AgentContext, AgentState


# ---------------------------------------------------------------------------
# 8 个 Tool 的处理函数
# ---------------------------------------------------------------------------


def _tool_get_video_info(context, state, args) -> dict[str, Any]:
    return _get_video_info(context.video.filepath)


def _tool_extract_audio(context, state, args) -> dict[str, Any]:
    path = _extract_audio(context.video.filepath, context.video.id)
    return {"audio_path": str(path)}


def _tool_transcribe_audio(context, state, args) -> dict[str, Any]:
    audio_path = args.get("audio_path")
    if not audio_path:
        prior = state.intermediate_results.get("extract_audio") or {}
        audio_path = prior.get("audio_path")
    if not audio_path:
        raise ToolError("缺少音频路径：请先调用 extract_audio（或传入 audio_path 参数）")
    result = get_transcription_service().transcribe(audio_path)
    return {
        "text": result.get("text"),
        "segments": result.get("segments") or [],
    }


def _tool_extract_keyframes(context, state, args) -> dict[str, Any]:
    interval = args.get("interval_seconds")
    kwargs: dict[str, Any] = {}
    if interval is not None:
        try:
            kwargs["interval_seconds"] = float(interval)
        except (TypeError, ValueError):
            raise ToolError(f"interval_seconds 必须是数字：{interval!r}")
    records = _extract_keyframes(context.video.filepath, context.video.id, **kwargs)
    return {
        "count": len(records),
        "frames": [
            {"timestamp": rec["timestamp"], "filepath": rec["filepath"]}
            for rec in records
        ],
    }


def _require_transcript(state) -> str:
    prior = state.intermediate_results.get("transcribe_audio")
    if not isinstance(prior, dict) or not isinstance(prior.get("text"), str):
        raise ToolError("缺少转写文本：请先调用 transcribe_audio")
    return prior["text"]


def _keyframe_paths(state) -> list[str]:
    prior = state.intermediate_results.get("extract_keyframes") or {}
    return [f["filepath"] for f in prior.get("frames") or []]


def _tool_generate_summary(context, state, args) -> dict[str, Any]:
    summary = generate_summary(_require_transcript(state), keyframes=_keyframe_paths(state))
    return {"summary": summary}


def _tool_generate_keywords(context, state, args) -> dict[str, Any]:
    keywords = generate_keywords(_require_transcript(state), keyframes=_keyframe_paths(state))
    return {"keywords": keywords}


def _tool_generate_chapters(context, state, args) -> dict[str, Any]:
    duration = context.video.duration
    chapters = generate_chapters(
        _require_transcript(state),
        duration=float(duration) if duration else None,
        keyframes=_keyframe_paths(state),
    )
    return {"chapters": chapters}


def build_result_fields(state) -> dict[str, str]:
    """从中间结果组装 `AnalysisResult` 写库字段（Text 列存 JSON 字符串）。

    缺任何一步的产出都抛 `ToolError`（save_result 与任务接线共用此校验）。
    """
    results = state.intermediate_results
    summary = (results.get("generate_summary") or {}).get("summary")
    keywords = (results.get("generate_keywords") or {}).get("keywords")
    chapters = (results.get("generate_chapters") or {}).get("chapters")
    transcript = (results.get("transcribe_audio") or {}).get("text")

    missing = [
        name
        for name, value in (
            ("transcribe_audio", transcript),
            ("generate_summary", summary),
            ("generate_keywords", keywords),
            ("generate_chapters", chapters),
        )
        if value is None
    ]
    if missing:
        raise ToolError(f"缺少分析结果：请先成功调用 {', '.join(missing)}")
    return {
        "summary": summary,
        "keywords": json.dumps(keywords, ensure_ascii=False),
        "chapters": json.dumps(chapters, ensure_ascii=False),
        "transcript": transcript,
    }


def _tool_save_result(context, state, args) -> dict[str, Any]:
    if context.task_id is None:
        raise ToolError("save_result 需要 task_id（当前上下文未关联分析任务）")
    fields = build_result_fields(state)
    ResultRepository(context.session).create(context.task_id, **fields)
    return {"saved": True, "task_id": context.task_id}


# ---------------------------------------------------------------------------
# 注册表：名称 → (输入 Schema, 处理函数)
# ---------------------------------------------------------------------------

_NO_PARAMS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

TOOLS: dict[str, ToolSpec] = {
    "get_video_info": ToolSpec(
        "get_video_info", dict(_NO_PARAMS), _tool_get_video_info
    ),
    "extract_audio": ToolSpec("extract_audio", dict(_NO_PARAMS), _tool_extract_audio),
    "transcribe_audio": ToolSpec(
        "transcribe_audio",
        {
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "可选：wav 音频路径；缺省用 extract_audio 的输出",
                }
            },
            "additionalProperties": False,
        },
        _tool_transcribe_audio,
    ),
    "extract_keyframes": ToolSpec(
        "extract_keyframes",
        {
            "type": "object",
            "properties": {
                "interval_seconds": {
                    "type": "number",
                    "description": "可选：抽帧间隔（秒），默认 5",
                }
            },
            "additionalProperties": False,
        },
        _tool_extract_keyframes,
    ),
    "generate_summary": ToolSpec(
        "generate_summary", dict(_NO_PARAMS), _tool_generate_summary
    ),
    "generate_keywords": ToolSpec(
        "generate_keywords", dict(_NO_PARAMS), _tool_generate_keywords
    ),
    "generate_chapters": ToolSpec(
        "generate_chapters", dict(_NO_PARAMS), _tool_generate_chapters
    ),
    "save_result": ToolSpec("save_result", dict(_NO_PARAMS), _tool_save_result),
}

# OpenAI function calling 格式（mimo 原生 Tool Calling）
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": prompts.TOOL_DESCRIPTIONS[spec.name],
            "parameters": spec.parameters,
        },
    }
    for spec in TOOLS.values()
]


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    context: "AgentContext",
    state: "AgentState",
) -> dict[str, Any]:
    """执行一个 Tool：成功结果记入 intermediate_results 并返回；失败抛 ToolError。"""
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError(f"未知工具：{name}")
    try:
        result = spec.handler(context, state, arguments or {})
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 服务层异常统一转 ToolError 回传 LLM
        raise ToolError(f"{name} 执行失败：{exc}") from exc
    if not isinstance(result, dict):
        raise ToolError(f"{name} 输出非法：应为 dict，实际 {type(result).__name__}")
    try:
        json.dumps(result, ensure_ascii=False)  # 必须能作为 tool 消息回传
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{name} 输出不可 JSON 序列化：{exc}") from exc
    state.intermediate_results[name] = result
    return result
