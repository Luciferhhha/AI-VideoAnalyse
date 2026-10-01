"""Agent 核心循环（计划书 10.3–10.6）。

循环：LLM → 选 Tool → 执行 → 回传结果 → LLM 判断 → 最终结果。

- `MAX_TOOL_CALLS = 10`（10.4）：超限终止并抛 AgentError，禁止无限调用。
- `AgentState`（10.5）：video_id / task_id / current_step / tool_calls /
  intermediate_results / errors，关键节点全程落日志。
- LLM 双实现：`MimoAgentLLM`（mimo 原生 Tool Calling，需 API Key）与
  `MockAgentLLM`（脚本化 Tool 序列，测试无 Key 可跑）。
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from app import config
from app.agent.prompts import AGENT_SYSTEM_PROMPT
from app.agent.tools import TOOL_SCHEMAS, execute_tool

logger = logging.getLogger(__name__)

# 10.4：单次 Agent 运行允许的最大工具调用次数
MAX_TOOL_CALLS = 10

# mimo API 请求失败时的指数退避上限（与阶段七/九一致）
_MAX_BACKOFF_SECONDS = 30.0


class AgentError(Exception):
    """Agent 循环失败（超限、LLM 调用失败、协议异常等）。"""

    def __init__(self, message: str, state: "AgentState | None" = None) -> None:
        super().__init__(message)
        self.state = state  # 终止时尽量带上现场，便于落日志与排错


@dataclass
class AgentContext:
    """Tool 执行所需的上下文：视频行、会话、关联任务（save_result 用）。"""

    video: Any  # app.database.models.Video
    session: Any  # sqlalchemy.orm.Session
    task_id: int | None = None


@dataclass
class AgentState:
    """10.5：Agent 全程状态（落日志、测试断言、任务接线共用）。"""

    video_id: int
    task_id: int | None = None
    current_step: str = "init"  # init / running / tool:<name> / finished / error
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    intermediate_results: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    final_answer: str | None = None


@dataclass
class ToolCall:
    """LLM 返回的一次工具调用（arguments 已解析为 dict）。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AgentDecision:
    """LLM 的一步决策：要么给出 tool_calls，要么给出最终答复 content。"""

    tool_calls: list[ToolCall] | None = None
    content: str | None = None


class AgentLLM(ABC):
    """Agent 用 LLM 统一接口：消息 + Tool Schema 进，一次决策出。"""

    @abstractmethod
    def decide(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AgentDecision:
        """返回 tool_calls 或最终 content；失败抛 AgentError。"""


class MimoAgentLLM(AgentLLM):
    """mimo 原生 Tool Calling（OpenAI 兼容 tools 参数，官方 openai-api 文档）。

    重试策略与阶段七转写/阶段九分析一致：429/5xx 可重试（Retry-After 优先，
    封顶 30s），401/400 等确定性失败不重试，超时/网络错误计入重试。
    """

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

    def decide(self, messages, tools) -> AgentDecision:
        if not self.api_key:
            raise AgentError(
                "未配置 MIMO_API_KEY（环境变量或 app/config.py），无法调用 mimo Agent API。"
            )
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
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
                        raise AgentError(
                            f"mimo Agent 请求被拒绝（HTTP {resp.status_code}）：{detail}"
                        )
                if attempt >= self.max_retries:
                    break
                delay = self._backoff_delay(attempt, retry_after)
                logger.warning(
                    "mimo Agent 第 %d 次尝试失败，%.1fs 后重试：%s",
                    attempt + 1, delay, last_error,
                )
                time.sleep(delay)

        raise AgentError(
            f"mimo Agent 请求失败（已尝试 {self.max_retries + 1} 次）：{last_error}"
        )

    def _backoff_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass  # HTTP-date 等格式不解析，退回指数退避
        return min(self.backoff_base * (2**attempt), _MAX_BACKOFF_SECONDS)

    def _parse_response(self, resp: httpx.Response) -> AgentDecision:
        """tool_calls 优先；否则取 content 作为最终答复（官方 openai-api 响应结构）。"""
        try:
            data = resp.json()
        except ValueError as exc:
            raise AgentError(
                f"mimo Agent 响应不是合法 JSON：{(resp.text or '')[:200]}"
            ) from exc
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentError(
                "mimo Agent 响应结构异常：缺少 choices[0].message"
            ) from exc

        raw_calls = message.get("tool_calls") or []
        if raw_calls:
            calls: list[ToolCall] = []
            for idx, raw in enumerate(raw_calls, start=1):
                fn = raw.get("function") or {}
                name = fn.get("name")
                if not name:
                    raise AgentError("mimo Agent 响应结构异常：tool_calls 缺少 function.name")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = (
                        json.loads(raw_args)
                        if isinstance(raw_args, str)
                        else dict(raw_args)
                    )
                except (json.JSONDecodeError, ValueError) as exc:
                    raise AgentError(
                        f"mimo Agent 返回的 tool 参数不是合法 JSON：{raw_args!r}"
                    ) from exc
                if not isinstance(args, dict):
                    raise AgentError(f"mimo Agent 返回的 tool 参数不是对象：{raw_args!r}")
                calls.append(
                    ToolCall(
                        id=raw.get("id") or f"call_{idx}",
                        name=str(name),
                        arguments=args,
                    )
                )
            return AgentDecision(tool_calls=calls)

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AgentError("mimo Agent 返回空内容且未选择工具")
        return AgentDecision(content=content)


class MockAgentLLM(AgentLLM):
    """脚本化 MockLLM（测试用，无 API Key）：按既定顺序发起 Tool 调用。

    默认脚本即完整分析链路的 8 个 Tool；脚本耗尽后返回最终答复。
    可注入自定义 script 测试部分流程 / 超限 / 异常路径。
    """

    DEFAULT_SCRIPT = [
        "get_video_info",
        "extract_audio",
        "transcribe_audio",
        "extract_keyframes",
        "generate_summary",
        "generate_keywords",
        "generate_chapters",
        "save_result",
    ]

    def __init__(
        self,
        script: list[str] | None = None,
        final_content: str = "分析完成：已生成视频摘要、关键词与章节，并写入结果表。",
    ) -> None:
        self.script = list(self.DEFAULT_SCRIPT if script is None else script)
        self.final_content = final_content
        self._seq = 0

    def decide(self, messages, tools) -> AgentDecision:
        if self.script:
            self._seq += 1
            name = self.script.pop(0)
            return AgentDecision(
                tool_calls=[ToolCall(id=f"call_{self._seq}", name=name, arguments={})]
            )
        return AgentDecision(content=self.final_content)


def get_agent_llm() -> AgentLLM:
    """按 config.AGENT_PROVIDER 构建 LLM（每次读配置，便于测试切换）。"""
    provider = (config.AGENT_PROVIDER or "mimo").strip().lower()
    if provider == "mimo":
        return MimoAgentLLM(
            api_key=config.MIMO_API_KEY,
            base_url=config.MIMO_BASE_URL,
            model=config.MIMO_AGENT_MODEL,
        )
    if provider == "mock":
        return MockAgentLLM()
    raise AgentError(f"未知的 Agent 提供方：{config.AGENT_PROVIDER}（可选 mimo / mock）")


def _user_message(context: AgentContext) -> str:
    info: dict[str, Any] = {
        "video_id": context.video.id,
        "filename": context.video.filename,
        "duration": context.video.duration,
    }
    if context.task_id is not None:
        info["task_id"] = context.task_id
    return (
        "请按系统提示的顺序完成这个视频的完整分析（含 save_result 保存结果）。\n"
        f"视频信息：{json.dumps(info, ensure_ascii=False)}"
    )


def _assistant_message(decision: AgentDecision) -> dict[str, Any]:
    """把带 tool_calls 的 assistant 消息补进对话历史（OpenAI 协议要求）。"""
    return {
        "role": "assistant",
        "content": decision.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in (decision.tool_calls or [])
        ],
    }


def run_agent(
    context: AgentContext,
    *,
    llm: AgentLLM | None = None,
    max_tool_calls: int = MAX_TOOL_CALLS,
) -> AgentState:
    """执行一次 Agent 循环，直到 LLM 给出最终答复或触发超限（抛 AgentError）。

    Tool 异常不外抛：记入 state.errors 并以 `{"error": …}` 回传 LLM 继续循环；
    超限（10.4）抛 AgentError（state 挂在异常上），由调用方落任务失败。
    """
    llm = llm or get_agent_llm()
    state = AgentState(
        video_id=context.video.id,
        task_id=context.task_id,
        current_step="running",
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": _user_message(context)},
    ]
    logger.info(
        "Agent 启动 video_id=%s task_id=%s max_tool_calls=%s",
        state.video_id, state.task_id, max_tool_calls,
    )

    while True:
        decision = llm.decide(messages, TOOL_SCHEMAS)
        if not decision.tool_calls:
            state.current_step = "finished"
            state.final_answer = decision.content
            logger.info(
                "Agent 完成 video_id=%s tool_calls=%s errors=%s",
                state.video_id, len(state.tool_calls), len(state.errors),
            )
            return state

        messages.append(_assistant_message(decision))
        for call in decision.tool_calls:
            # 10.4：上限检查发生在每次执行前，超限立即终止
            if len(state.tool_calls) >= max_tool_calls:
                state.current_step = "error"
                message = f"工具调用超出上限（最多 {max_tool_calls} 次），已终止"
                state.errors.append(message)
                logger.error(
                    "Agent 超限终止 video_id=%s tool_calls=%s",
                    state.video_id, len(state.tool_calls),
                )
                raise AgentError(message, state=state)

            state.current_step = f"tool:{call.name}"
            state.tool_calls.append(
                {"id": call.id, "name": call.name, "arguments": call.arguments}
            )
            logger.info(
                "Agent 工具调用 video_id=%s tool=%s args=%s",
                state.video_id, call.name,
                json.dumps(call.arguments, ensure_ascii=False),
            )
            try:
                result = execute_tool(call.name, call.arguments, context, state)
            except Exception as exc:  # noqa: BLE001 —— ToolError/未知异常都回传 LLM，不崩溃
                state.errors.append(f"{call.name}: {exc}")
                logger.warning(
                    "Agent 工具失败 video_id=%s tool=%s error=%s",
                    state.video_id, call.name, exc,
                )
                result = {"error": str(exc)}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
