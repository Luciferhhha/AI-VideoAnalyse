"""阶段十（10.7）：Agent 单元/流程测试 —— Tool 单测、MockLLM 全流程、超限与异常。

全部无需 API Key：LLM 走 MockAgentLLM / httpx.MockTransport，
转写与分析走 mock provider（见本文件 agent_env fixture）。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app import config
from app.agent import agent as agent_module
from app.agent.agent import (
    MAX_TOOL_CALLS,
    AgentContext,
    AgentError,
    AgentState,
    MimoAgentLLM,
    MockAgentLLM,
    get_agent_llm,
    run_agent,
)
from app.agent.prompts import AGENT_SYSTEM_PROMPT, TOOL_DESCRIPTIONS
from app.agent.tools import TOOLS, TOOL_SCHEMAS, ToolError, build_result_fields, execute_tool
from app.database.database import Base, create_db_engine
from app.database.repository import ResultRepository, TaskRepository, VideoRepository
from app.services.transcription_service import MockTranscriptionService


@pytest.fixture()
def agent_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_video: Path):
    """独立 SQLite + 视频行 + 任务行；输出目录与 provider 全部隔离。"""
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "TRANSCRIPTION_PROVIDER", "mock")
    monkeypatch.setattr(config, "ANALYSIS_PROVIDER", "mock")
    monkeypatch.setattr(config, "AGENT_PROVIDER", "mock")

    engine = create_db_engine(f"sqlite:///{tmp_path.as_posix()}/agent.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    video = VideoRepository(session).create(
        filename="agent_sample.mp4",
        filepath=str(sample_video),
        duration=2.0,
        width=320,
        height=240,
        fps=10.0,
    )
    task = TaskRepository(session).create(video.id)
    context = AgentContext(video=video, session=session, task_id=task.id)
    state = AgentState(video_id=video.id, task_id=task.id)
    yield SimpleNamespace(
        context=context, state=state, session=session, video=video, task=task
    )
    session.close()
    engine.dispose()


def _fresh_state(env) -> AgentState:
    return AgentState(video_id=env.video.id, task_id=env.task.id)


# ---------------------------------------------------------------- Tool 契约


def test_tool_registry_has_all_eight_tools() -> None:
    assert len(TOOLS) == 8
    assert set(TOOLS) == set(TOOL_DESCRIPTIONS)
    assert len(TOOL_SCHEMAS) == 8
    for spec in TOOL_SCHEMAS:
        assert spec["type"] == "function"
        assert spec["function"]["name"] in TOOLS


def test_system_prompt_lists_tools_in_dependency_order() -> None:
    assert "get_video_info" in AGENT_SYSTEM_PROMPT
    assert "save_result" in AGENT_SYSTEM_PROMPT
    # save_result 的描述必须强调"最后一步"（10.2 依赖顺序约定）
    assert "最后一步" in TOOL_DESCRIPTIONS["save_result"]


def test_get_video_info_tool(agent_env) -> None:
    result = execute_tool("get_video_info", {}, agent_env.context, _fresh_state(agent_env))
    # filename 取自 ffprobe（真实文件名），不是我们写库时用的名字
    assert result["filename"] == "sample.mp4"
    assert result["duration"] == 2.0


def test_extract_audio_then_transcribe(agent_env) -> None:
    state = _fresh_state(agent_env)
    audio = execute_tool("extract_audio", {}, agent_env.context, state)
    assert Path(audio["audio_path"]).is_file()
    assert audio["audio_path"].endswith(".wav")

    transcript = execute_tool("transcribe_audio", {}, agent_env.context, state)
    assert transcript["text"] == MockTranscriptionService.MOCK_TEXT
    assert isinstance(transcript["segments"], list)
    # 依赖自动取上一步的 audio_path（10.2 描述约定）
    assert state.intermediate_results["extract_audio"]["audio_path"] == audio["audio_path"]


def test_transcribe_without_extract_raises(agent_env) -> None:
    with pytest.raises(ToolError, match="缺少音频路径"):
        execute_tool("transcribe_audio", {}, agent_env.context, _fresh_state(agent_env))


def test_extract_keyframes_tool(agent_env) -> None:
    result = execute_tool(
        "extract_keyframes", {"interval_seconds": 1.0},
        agent_env.context, _fresh_state(agent_env),
    )
    assert result["count"] >= 1
    assert Path(result["frames"][0]["filepath"]).is_file()


def test_generate_summary_requires_transcript(agent_env) -> None:
    with pytest.raises(ToolError, match="缺少转写文本"):
        execute_tool("generate_summary", {}, agent_env.context, _fresh_state(agent_env))


def _prepare_analysis(env) -> AgentState:
    """跑完前置四步，得到可 save_result 的 state。"""
    state = _fresh_state(env)
    for name, args in [
        ("extract_audio", {}),
        ("transcribe_audio", {}),
        ("extract_keyframes", {}),
        ("generate_summary", {}),
        ("generate_keywords", {}),
        ("generate_chapters", {}),
    ]:
        execute_tool(name, args, env.context, state)
    return state


def test_generate_tools_and_build_result_fields(agent_env) -> None:
    state = _prepare_analysis(agent_env)
    assert state.intermediate_results["generate_summary"]["summary"]
    keywords = json.loads(json.dumps(state.intermediate_results["generate_keywords"]["keywords"]))
    assert isinstance(keywords, list) and keywords
    chapters = state.intermediate_results["generate_chapters"]["chapters"]
    assert set(chapters[0]) == {"start", "title", "summary"}

    fields = build_result_fields(state)
    assert set(fields) == {"summary", "keywords", "chapters", "transcript"}
    assert json.loads(fields["keywords"]) == keywords
    assert json.loads(fields["chapters"])[0]["start"] == chapters[0]["start"]


def test_build_result_fields_requires_all_steps(agent_env) -> None:
    state = _fresh_state(agent_env)
    state.intermediate_results["transcribe_audio"] = {"text": "x", "segments": []}
    with pytest.raises(ToolError, match="缺少分析结果"):
        build_result_fields(state)


def test_save_result_requires_task_id(agent_env) -> None:
    state = _prepare_analysis(agent_env)
    no_task = AgentContext(video=agent_env.video, session=agent_env.context.session, task_id=None)
    with pytest.raises(ToolError, match="task_id"):
        execute_tool("save_result", {}, no_task, state)


def test_save_result_writes_analysis_result(agent_env) -> None:
    state = _prepare_analysis(agent_env)
    result = execute_tool("save_result", {}, agent_env.context, state)
    assert result["saved"] is True and result["task_id"] == agent_env.task.id
    row = ResultRepository(agent_env.context.session).get_by_task(agent_env.task.id)
    assert row is not None
    assert row.transcript == MockTranscriptionService.MOCK_TEXT
    assert json.loads(row.keywords)


def test_unknown_tool_raises(agent_env) -> None:
    with pytest.raises(ToolError, match="未知工具"):
        execute_tool("does_not_exist", {}, agent_env.context, _fresh_state(agent_env))


# ---------------------------------------------------------------- Agent 流程


def test_run_agent_full_flow_with_mock_llm(agent_env) -> None:
    state = run_agent(agent_env.context, llm=MockAgentLLM())
    assert state.current_step == "finished"
    assert state.errors == []
    assert state.final_answer  # 10.3：最终中文总结
    assert len(state.tool_calls) == 8
    assert [c["name"] for c in state.tool_calls] == MockAgentLLM.DEFAULT_SCRIPT
    # save_result 落库
    row = ResultRepository(agent_env.context.session).get_by_task(agent_env.task.id)
    assert row is not None and row.summary


def test_run_agent_over_limit_terminates(agent_env) -> None:
    llm = MockAgentLLM(script=["get_video_info"] * 5)
    with pytest.raises(AgentError, match="超出上限") as excinfo:
        run_agent(agent_env.context, llm=llm, max_tool_calls=3)
    state = excinfo.value.state
    assert state is not None
    assert state.current_step == "error"
    assert len(state.tool_calls) == 3  # 达到上限即终止，不再多执行
    assert any("超出上限" in e for e in state.errors)


def test_max_tool_calls_default_is_ten() -> None:
    assert MAX_TOOL_CALLS == 10  # 10.4 硬性约定


def test_tool_failure_reported_and_loop_continues(agent_env) -> None:
    # 第一步就失败（无转写直接生成摘要），错误回传 LLM 后循环继续到脚本结束
    llm = MockAgentLLM(script=["generate_summary", "get_video_info"])
    state = run_agent(agent_env.context, llm=llm)
    assert state.current_step == "finished"
    assert state.errors and "缺少转写文本" in state.errors[0]
    assert len(state.tool_calls) == 2  # 失败不中断，后续工具仍执行


def test_unknown_tool_failure_is_captured(agent_env) -> None:
    llm = MockAgentLLM(script=["no_such_tool"])
    state = run_agent(agent_env.context, llm=llm)
    assert state.errors and "未知工具" in state.errors[0]


def test_agent_state_tracks_steps_and_results(agent_env) -> None:
    state = run_agent(agent_env.context, llm=MockAgentLLM())
    assert state.video_id == agent_env.video.id
    assert state.task_id == agent_env.task.id
    assert set(state.intermediate_results) >= {
        "extract_audio", "transcribe_audio", "generate_summary",
        "generate_keywords", "generate_chapters", "save_result",
    }


# ---------------------------------------------------------------- Mimo Agent LLM


def _tool_call_response(name: str, arguments: dict) -> dict:
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }],
            },
        }],
    }


def _llm(handler, **kwargs) -> MimoAgentLLM:
    return MimoAgentLLM(
        api_key=kwargs.pop("api_key", "test-key"),
        transport=httpx.MockTransport(handler),
        backoff_base=0.0,
        **kwargs,
    )


def test_mimo_agent_llm_requires_api_key() -> None:
    with pytest.raises(AgentError, match="MIMO_API_KEY"):
        MimoAgentLLM(api_key="").decide([], [])


def test_mimo_agent_llm_parses_tool_calls() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_tool_call_response("get_video_info", {}))

    llm = _llm(handler)
    decision = llm.decide([{"role": "user", "content": "hi"}], TOOL_SCHEMAS)
    assert [c.name for c in decision.tool_calls] == ["get_video_info"]
    assert decision.tool_calls[0].arguments == {}
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["tools"] == TOOL_SCHEMAS


def test_mimo_agent_llm_parses_final_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "分析完成"}}]}
        )

    decision = _llm(handler).decide([], TOOL_SCHEMAS)
    assert decision.tool_calls is None
    assert decision.content == "分析完成"


def test_mimo_agent_llm_retries_429_then_succeeds(
    agent_env, monkeypatch
) -> None:
    attempts = {"n": 0}
    sleeps: list[float] = []
    monkeypatch.setattr(agent_module.time, "sleep", sleeps.append)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1.5"}, text="rate limited")
        return httpx.Response(200, json=_tool_call_response("get_video_info", {}))

    decision = _llm(handler).decide([], TOOL_SCHEMAS)
    assert attempts["n"] == 2
    assert sleeps == [1.5]
    assert decision.tool_calls[0].name == "get_video_info"


def test_mimo_agent_llm_401_fails_fast() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(AgentError, match="被拒绝（HTTP 401）"):
        _llm(handler).decide([], TOOL_SCHEMAS)
    assert attempts["n"] == 1  # 确定性 4xx 不重试


def test_mimo_agent_llm_exhausted_retries(monkeypatch) -> None:
    monkeypatch.setattr(agent_module.time, "sleep", lambda _d: None)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(500, text="server error")

    with pytest.raises(AgentError, match="已尝试 3 次"):
        _llm(handler).decide([], TOOL_SCHEMAS)
    assert attempts["n"] == 3


def test_mimo_agent_llm_bad_tool_json_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_tool_call_response("x", {}) | {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "c1",
                        "function": {"name": "x", "arguments": "not json{"},
                    }],
                },
            }],
        })

    with pytest.raises(AgentError, match="不是合法 JSON"):
        _llm(handler).decide([], TOOL_SCHEMAS)


def test_mimo_agent_llm_empty_response_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]})

    with pytest.raises(AgentError, match="空内容"):
        _llm(handler).decide([], TOOL_SCHEMAS)


# ---------------------------------------------------------------- 工厂


def test_get_agent_llm_factory(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_PROVIDER", "mock")
    assert isinstance(get_agent_llm(), MockAgentLLM)

    monkeypatch.setattr(config, "AGENT_PROVIDER", "mimo")
    monkeypatch.setattr(config, "MIMO_API_KEY", "k")
    llm = get_agent_llm()
    assert isinstance(llm, MimoAgentLLM)
    assert llm.model == config.MIMO_AGENT_MODEL

    monkeypatch.setattr(config, "AGENT_PROVIDER", "deepl")
    with pytest.raises(AgentError, match="未知的 Agent 提供方"):
        get_agent_llm()


def test_agent_failure_without_key() -> None:
    with pytest.raises(AgentError, match="MIMO_API_KEY"):
        run_agent(
            AgentContext(video=SimpleNamespace(id=1, filename="f.mp4", duration=1.0),
                         session=None, task_id=None),
            llm=MimoAgentLLM(api_key=""),
        )
