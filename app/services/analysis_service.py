"""AI 内容分析服务（阶段九）：mimo API 多模态（文本 + 关键帧图像）+ MockLLM。

计划书第十二章 9.1~9.5：
- 9.1 `generate_summary` / `generate_keywords` / `generate_chapters`，
  第一版调用 mimo API（OpenAI Chat Completion 兼容），输入 = 转写文本 +
  关键帧图像（data URL `image_url`，官方图像理解文档）；
- 9.2 LLM 返回结构化 JSON → Pydantic 校验（`app/schemas/analysis.py`）；
  非法 JSON：尝试修复（去代码围栏 / 括号配平截取 / 去尾逗号）→ 仍失败抛
  `AnalysisServiceError` → 任务 failed 并记录原因，**程序不崩溃**；
- 9.3 章节格式 `[{"start":"00:00","title","summary"}]`；
- 9.5 测试全部走 MockLLM（固定 JSON），无 API Key 也能全绿。

工厂 `get_analysis_llm()` 按 `config.ANALYSIS_PROVIDER` 在 mimo / mock 间切换；
mimo 实现的超时/限流退避重试策略与 transcription_service 同源
（429/5xx/网络错误/超时重试，401/400 等 4xx 确定性失败不重试）。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app import config
from app.schemas.analysis import (
    ChaptersPayload,
    KeywordsPayload,
    SummaryPayload,
)

logger = logging.getLogger(__name__)

# 重试策略与 transcription_service 一致：429/5xx/网络错误/超时可重试，
# 401/400 等 4xx 属确定性失败；退避有上限，Retry-After 优先。
_MAX_BACKOFF_SECONDS = 30.0

# 关键帧图像输入上限（均匀取帧）：控制图像 token 成本与上下文长度
_MAX_KEYFRAMES_IN_PROMPT = 8

_PayloadT = TypeVar("_PayloadT", bound=BaseModel)


class AnalysisServiceError(Exception):
    """AI 分析失败（原因写入 task.error_message）。"""


# ---------------------------------------------------------------------------
# LLM 抽象与实现（9.5 MockLLM / mimo API）
# ---------------------------------------------------------------------------


class AnalysisLLM(ABC):
    """分析用 LLM 统一接口：消息进、assistant content 字符串出。"""

    @abstractmethod
    def complete(self, messages: list[dict[str, Any]]) -> str:
        """发送 chat messages，返回 content；失败抛 AnalysisServiceError。"""


class MimoAnalysisLLM(AnalysisLLM):
    """mimo API 多模态分析（视觉模型，文本 + 关键帧图像 data URL 输入）。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.xiaomimimo.com/v1",
        model: str = "mimo-v2.6-flash",
        timeout: float = 60.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.transport = transport  # 测试注入 httpx.MockTransport

    def complete(self, messages: list[dict[str, Any]]) -> str:
        if not self.api_key:
            # 框架先行（《初版建议》）：提示用户填写 API Key
            raise AnalysisServiceError(
                "未配置 MIMO_API_KEY（环境变量或 app/config.py），无法调用 mimo 分析 API。"
            )
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        last_error = "未知错误"
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            for attempt in range(self.max_retries + 1):
                retry_after: str | None = None
                try:
                    resp = client.post(url, headers=headers, json=payload)
                except httpx.TimeoutException as exc:
                    last_error = f"请求超时（{self.timeout} 秒）：{exc}"
                except httpx.HTTPError as exc:
                    last_error = f"网络错误：{exc}"
                else:
                    if resp.status_code == 200:
                        return self._parse_response(resp)
                    detail = (resp.text or "").strip().replace("\n", " ")[-300:]
                    if resp.status_code == 429 or resp.status_code >= 500:
                        last_error = f"HTTP {resp.status_code}：{detail}"
                        retry_after = resp.headers.get("Retry-After")
                    else:
                        # 401/400 等：确定性失败，不重试
                        raise AnalysisServiceError(
                            f"mimo 分析请求被拒绝（HTTP {resp.status_code}）：{detail}"
                        )
                if attempt >= self.max_retries:
                    break
                delay = self._backoff_delay(attempt, retry_after)
                logger.warning(
                    "mimo 分析第 %d 次尝试失败，%.1fs 后重试：%s",
                    attempt + 1, delay, last_error,
                )
                time.sleep(delay)

        raise AnalysisServiceError(
            f"mimo 分析请求失败（已尝试 {self.max_retries + 1} 次）：{last_error}"
        )

    def _backoff_delay(self, attempt: int, retry_after: str | None) -> float:
        """指数退避；响应带 Retry-After 时优先采用（封顶 _MAX_BACKOFF_SECONDS）。"""
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass  # HTTP-date 等格式不解析，退回指数退避
        return min(self.backoff_base * (2**attempt), _MAX_BACKOFF_SECONDS)

    def _parse_response(self, resp: httpx.Response) -> str:
        """取 choices[0].message.content（官方图像理解响应为纯字符串）。"""
        try:
            data = resp.json()
        except ValueError as exc:
            raise AnalysisServiceError(
                f"mimo 分析响应不是合法 JSON：{(resp.text or '')[:200]}"
            ) from exc
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AnalysisServiceError(
                "mimo 分析响应结构异常：缺少 choices[0].message.content"
            ) from exc
        if not isinstance(text, str) or not text.strip():
            raise AnalysisServiceError("mimo 分析返回空内容")
        return text


class MockAnalysisLLM(AnalysisLLM):
    """MockLLM（9.5）：按消息中的 TASK 标记返回固定 JSON，无 API Key 可用。

    prompt 的 system 段固定携带 `TASK=summary|keywords|chapters` 标记，
    Mock 据此返回对应契约的 JSON；真实 mimo 模型视其为普通指令文本，无影响。
    """

    MOCK_SUMMARY = "这是由 Mock LLM 生成的视频摘要，用于无 API Key 的测试。"
    MOCK_KEYWORDS = ["测试", "视频分析", "MockLLM"]
    MOCK_CHAPTERS = [
        {"start": "00:00", "title": "开场", "summary": "Mock 章节一：视频开场部分。"},
        {"start": "01:00", "title": "展开", "summary": "Mock 章节二：内容展开部分。"},
    ]

    def complete(self, messages: list[dict[str, Any]]) -> str:
        blob = json.dumps(messages, ensure_ascii=False)
        match = re.search(r"TASK=(\w+)", blob)
        task = match.group(1) if match else "summary"
        if task == "keywords":
            return json.dumps({"keywords": self.MOCK_KEYWORDS}, ensure_ascii=False)
        if task == "chapters":
            return json.dumps({"chapters": self.MOCK_CHAPTERS}, ensure_ascii=False)
        return json.dumps({"summary": self.MOCK_SUMMARY}, ensure_ascii=False)


def get_analysis_llm() -> AnalysisLLM:
    """按 config.ANALYSIS_PROVIDER 构建 LLM（每次调用读配置，便于切换）。"""
    provider = (config.ANALYSIS_PROVIDER or "mimo").strip().lower()
    if provider == "mimo":
        return MimoAnalysisLLM(
            api_key=config.MIMO_API_KEY,
            base_url=config.MIMO_BASE_URL,
            model=config.MIMO_ANALYSIS_MODEL,
        )
    if provider == "mock":
        return MockAnalysisLLM()
    raise AnalysisServiceError(
        f"未知的分析服务提供方：{config.ANALYSIS_PROVIDER!r}（可选 mimo / mock）"
    )


# ---------------------------------------------------------------------------
# JSON 解析与修复（9.2）
# ---------------------------------------------------------------------------


def _strip_code_fence(text: str) -> str:
    """去掉 ```json … ``` / ``` … ``` 代码围栏。"""
    match = re.search(r"```[a-zA-Z]*\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1) if match else text


def _strip_trailing_commas(text: str) -> str:
    """去掉对象/数组字面量中的尾逗号：`{…,}` → `{…}`。"""
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _extract_first_object(text: str) -> str | None:
    """截取第一个括号配平的 `{…}` 对象；截断时按未闭合层级补齐 `]`/`}` 修复。

    用栈同时跟踪 `{}` 与 `[]`：LLM 截断常见于嵌套数组（章节列表），
    只补 `}` 会留下未闭合的 `[`，修复无效。
    """
    start = text.find("{")
    if start < 0:
        return None
    stack: list[str] = []
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
            # 括号不匹配（如 } 关 [）：跳过，交由下一层候选/失败处理
            if not stack:
                return text[start : i + 1]
    # 截断且不在字符串中间：按剩余未闭合层级补右括号作为修复尝试
    if not in_str and stack:
        return text[start:] + "".join(reversed(stack))
    return None


def parse_llm_json(raw: str, payload_model: type[_PayloadT]) -> _PayloadT:
    """解析 LLM 返回并做 Pydantic 校验（9.2）。

    修复顺序：直解 → 去代码围栏 → 括号配平截取（含补右括号）→
    对每个候选再去一次尾逗号；JSON 合法但契约不符时直接抛
    `AnalysisServiceError`（结构问题修复无益）；全部失败也抛
    `AnalysisServiceError` —— 调用方（任务链路）负责落库，程序不崩溃。
    """
    text = (raw or "").strip()
    candidates: list[str] = [text]
    fenced = _strip_code_fence(text)
    if fenced.strip() and fenced not in candidates:
        candidates.append(fenced)
    obj = _extract_first_object(fenced if fenced.strip() else text)
    if obj and obj not in candidates:
        candidates.append(obj)
    expanded: list[str] = []
    for cand in candidates:
        if not cand:
            continue
        expanded.append(cand)
        fixed = _strip_trailing_commas(cand)
        if fixed != cand:
            expanded.append(fixed)

    last_reason = "空响应"
    for cand in expanded:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError as exc:
            last_reason = str(exc)
            continue
        try:
            return payload_model.model_validate(data)
        except ValidationError as exc:
            raise AnalysisServiceError(
                f"LLM 返回的 JSON 不符合契约 {payload_model.__name__}：{exc}"
            ) from exc
    raise AnalysisServiceError(
        f"LLM 返回非法 JSON（尝试修复 {len(expanded)} 个候选后仍失败）：{last_reason}"
    )


# ---------------------------------------------------------------------------
# 消息构造：转写文本 + 关键帧图像（9.1）
# ---------------------------------------------------------------------------

_TASK_INSTRUCTIONS = {
    "summary": "TASK=summary：根据转写文本与关键帧生成一段中文视频摘要。",
    "keywords": "TASK=keywords：根据转写文本与关键帧提取若干中文关键词。",
    "chapters": "TASK=chapters：根据转写文本与关键帧为视频划分章节。",
}

_TASK_CONTRACTS = {
    "summary": '输出 JSON：{"summary": "……"}',
    "keywords": '输出 JSON：{"keywords": ["词1", "词2", …]}',
    "chapters": (
        '输出 JSON：{"chapters": [{"start": "00:00", "title": "……", '
        '"summary": "……"}]}；start 为 MM:SS，按时间递增且不超过视频总时长。'
    ),
}


def _image_data_url(path: str | Path) -> str:
    """关键帧文件 → data URL（官方格式 data:image/jpeg;base64,…）。"""
    p = Path(path)
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(p.suffix.lower())
    if mime is None:
        raise AnalysisServiceError(f"不支持的关键帧格式：{p.name}")
    if not p.is_file():
        raise AnalysisServiceError(f"关键帧文件不存在：{p}")
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise AnalysisServiceError(f"读取关键帧失败：{p} —— {exc}") from exc
    if not raw:
        raise AnalysisServiceError(f"关键帧文件为空：{p.name}")
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def select_keyframes(
    keyframes: Sequence[str | Path], max_frames: int = _MAX_KEYFRAMES_IN_PROMPT
) -> list[Path]:
    """均匀取帧（保留首尾），控制图像 token 成本与上下文长度。"""
    paths = [Path(k) for k in keyframes]
    if max_frames <= 0 or not paths:
        return []
    if len(paths) <= max_frames:
        return paths
    if max_frames == 1:
        return [paths[0]]
    n = len(paths)
    picked: list[Path] = []
    seen: set[int] = set()
    for i in range(max_frames):
        idx = round(i * (n - 1) / (max_frames - 1))
        if idx not in seen:
            seen.add(idx)
            picked.append(paths[idx])
    return picked


def _build_messages(
    task: str,
    transcript: str,
    *,
    keyframes: Sequence[str | Path] | None = None,
    extra_text: str | None = None,
) -> list[dict[str, Any]]:
    """构造 OpenAI chat 消息：system（含 TASK 标记 + 纯 JSON 要求）+
    user content（关键帧图像在前、任务文本在后，依官方图像理解文档）。"""
    system = (
        f"{_TASK_INSTRUCTIONS[task]} "
        "只返回一个 JSON 对象，不要输出任何解释文字或 Markdown 代码围栏。"
    )
    parts: list[str] = []
    if extra_text:
        parts.append(extra_text)
    parts.append(f"转写文本：\n{transcript or '（无转写文本）'}")
    parts.append(_TASK_CONTRACTS[task])
    user_parts: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": _image_data_url(k)}}
        for k in select_keyframes(keyframes or [])
    ]
    user_parts.append({"type": "text", "text": "\n\n".join(parts)})
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_parts},
    ]


# ---------------------------------------------------------------------------
# 三个生成函数（9.1）
# ---------------------------------------------------------------------------


def generate_summary(
    transcript: str,
    *,
    keyframes: Sequence[str | Path] | None = None,
    llm: AnalysisLLM | None = None,
) -> str:
    """生成视频摘要（契约 `{"summary": str}` → str）；失败抛 AnalysisServiceError。"""
    llm = llm or get_analysis_llm()
    raw = llm.complete(_build_messages("summary", transcript, keyframes=keyframes))
    return parse_llm_json(raw, SummaryPayload).summary


def generate_keywords(
    transcript: str,
    *,
    keyframes: Sequence[str | Path] | None = None,
    llm: AnalysisLLM | None = None,
) -> list[str]:
    """提取视频关键词（契约 `{"keywords": [str, …]}` → list[str]）。"""
    llm = llm or get_analysis_llm()
    raw = llm.complete(_build_messages("keywords", transcript, keyframes=keyframes))
    return parse_llm_json(raw, KeywordsPayload).keywords


def generate_chapters(
    transcript: str,
    *,
    duration: float | None = None,
    keyframes: Sequence[str | Path] | None = None,
    llm: AnalysisLLM | None = None,
) -> list[dict[str, str]]:
    """划分章节（9.3，契约 `{"chapters": [{"start","title","summary"}]}`）。"""
    llm = llm or get_analysis_llm()
    extra = f"视频总时长（秒）：{duration:.0f}" if duration else None
    raw = llm.complete(
        _build_messages(
            "chapters", transcript, keyframes=keyframes, extra_text=extra
        )
    )
    payload = parse_llm_json(raw, ChaptersPayload)
    return [item.model_dump() for item in payload.chapters]
