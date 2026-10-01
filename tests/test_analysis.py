"""阶段九测试：AI 内容分析（9.1~9.5）。

- 全部走 MockLLM（9.5），无 API Key 可跑；
- 覆盖：三个生成函数契约、JSON 修复链与 Pydantic 校验（9.2）、
  工厂切换、mimo 客户端请求形状/重试/4xx fail-fast（httpx.MockTransport）、
  关键帧均匀取帧与 data URL 构造、章节格式（9.3）。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app import config
from app.schemas.analysis import ChaptersPayload, KeywordsPayload, SummaryPayload
from app.services import analysis_service as svc
from app.services.analysis_service import (
    AnalysisServiceError,
    MimoAnalysisLLM,
    MockAnalysisLLM,
    generate_chapters,
    generate_keywords,
    generate_summary,
    get_analysis_llm,
    parse_llm_json,
    select_keyframes,
)

# ---------------------------------------------------------------------------
# 9.5 MockLLM 全路径
# ---------------------------------------------------------------------------


def test_mock_llm_generates_summary_keywords_chapters() -> None:
    llm = MockAnalysisLLM()
    summary = generate_summary("一些转写文本", llm=llm)
    assert summary == MockAnalysisLLM.MOCK_SUMMARY

    keywords = generate_keywords("一些转写文本", llm=llm)
    assert keywords == MockAnalysisLLM.MOCK_KEYWORDS

    chapters = generate_chapters("一些转写文本", duration=120.0, llm=llm)
    assert chapters == MockAnalysisLLM.MOCK_CHAPTERS
    # 9.3 章节格式
    for ch in chapters:
        assert set(ch) == {"start", "title", "summary"}
        assert ch["start"].count(":") == 1


def test_generate_messages_carry_task_marker_images_and_duration(
    tmp_path: Path,
) -> None:
    """消息构造：system 含 TASK 标记、user 图像在前文本在后、章节含总时长。"""
    frame = tmp_path / "frame_0001.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xe0fakejpegdata")

    messages = svc._build_messages(
        "chapters",
        "转写内容……",
        keyframes=[frame],
        extra_text="视频总时长（秒）：90",
    )
    assert "TASK=chapters" in messages[0]["content"]
    user_parts = messages[1]["content"]
    assert user_parts[0]["type"] == "image_url"
    assert user_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    text_parts = [p for p in user_parts if p["type"] == "text"]
    assert len(text_parts) == 1
    assert "视频总时长（秒）：90" in text_parts[0]["text"]
    assert "转写内容……" in text_parts[0]["text"]
    assert '"chapters"' in text_parts[0]["text"]  # 契约说明


# ---------------------------------------------------------------------------
# 9.2 JSON 解析、修复与 Pydantic 校验
# ---------------------------------------------------------------------------


def test_parse_llm_json_accepts_plain_json() -> None:
    payload = parse_llm_json('{"summary": "好片"}', SummaryPayload)
    assert payload.summary == "好片"


def test_parse_llm_json_repairs_code_fence() -> None:
    raw = '```json\n{"summary": "带围栏的摘要"}\n```'
    assert parse_llm_json(raw, SummaryPayload).summary == "带围栏的摘要"


def test_parse_llm_json_repairs_trailing_comma_and_prose() -> None:
    raw = '好的，以下是结果：{"keywords": ["a", "b",],} 希望有帮助'
    payload = parse_llm_json(raw, KeywordsPayload)
    assert payload.keywords == ["a", "b"]


def test_parse_llm_json_repairs_unclosed_object() -> None:
    raw = '```json\n{"chapters": [{"start": "00:00", "title": "开", "summary": "始"}\n```'
    payload = parse_llm_json(raw, ChaptersPayload)
    assert payload.chapters[0].start == "00:00"


def test_parse_llm_json_raises_on_garbage() -> None:
    with pytest.raises(AnalysisServiceError, match="非法 JSON"):
        parse_llm_json("这不是 JSON 也没有括号", SummaryPayload)


def test_parse_llm_json_raises_on_contract_violation() -> None:
    # JSON 合法但契约不符（缺 summary）：修复无益，直接报契约错误
    with pytest.raises(AnalysisServiceError, match="不符合契约"):
        parse_llm_json('{"foo": 1}', SummaryPayload)


def test_parse_llm_json_rejects_bad_chapter_start() -> None:
    raw = json.dumps(
        {"chapters": [{"start": "xx:yy", "title": "t", "summary": "s"}]}
    )
    with pytest.raises(AnalysisServiceError, match="不符合契约"):
        parse_llm_json(raw, ChaptersPayload)


def test_generate_summary_propagates_invalid_llm_output() -> None:
    class BadLLM(MockAnalysisLLM):
        def complete(self, messages: list) -> str:
            return "no json at all"

    with pytest.raises(AnalysisServiceError):
        generate_summary("文本", llm=BadLLM())


# ---------------------------------------------------------------------------
# 工厂（config.ANALYSIS_PROVIDER 切换）
# ---------------------------------------------------------------------------


def test_get_analysis_llm_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ANALYSIS_PROVIDER", "mock")
    assert isinstance(get_analysis_llm(), MockAnalysisLLM)

    monkeypatch.setattr(config, "ANALYSIS_PROVIDER", "mimo")
    monkeypatch.setattr(config, "MIMO_API_KEY", "sk-test")
    llm = get_analysis_llm()
    assert isinstance(llm, MimoAnalysisLLM)
    assert llm.api_key == "sk-test"
    assert llm.model == config.MIMO_ANALYSIS_MODEL

    monkeypatch.setattr(config, "ANALYSIS_PROVIDER", "deepl")
    with pytest.raises(AnalysisServiceError, match="未知的分析服务提供方"):
        get_analysis_llm()


def test_generate_defaults_read_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """不显式传 llm 时按 config 构建（client fixture 里即 mock）。"""
    monkeypatch.setattr(config, "ANALYSIS_PROVIDER", "mock")
    assert generate_summary("文本") == MockAnalysisLLM.MOCK_SUMMARY


# ---------------------------------------------------------------------------
# MimoAnalysisLLM：请求形状与重试策略（httpx.MockTransport）
# ---------------------------------------------------------------------------


def make_llm(handler, **kwargs) -> MimoAnalysisLLM:
    kwargs.setdefault("api_key", "sk-test")
    return MimoAnalysisLLM(transport=httpx.MockTransport(handler), **kwargs)


def test_mimo_request_shape_and_content_parsing(tmp_path: Path) -> None:
    """请求：官方端点 + Bearer + model + 图像在前文本在后；响应取 content。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["payload"] = json.loads(request.content)
        content = json.loads(request.content)["messages"][1]["content"]
        seen["part_types"] = [p["type"] for p in content]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": ' {"summary": "真实摘要"} '}}]},
        )

    llm = make_llm(handler)
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"\xff\xd8data")
    raw = llm.complete(
        svc._build_messages("summary", "转写", keyframes=[frame])
    )
    assert parse_llm_json(raw, SummaryPayload).summary == "真实摘要"
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer sk-test"
    assert seen["payload"]["model"] == config.MIMO_ANALYSIS_MODEL
    assert seen["payload"]["stream"] is False
    assert seen["part_types"] == ["image_url", "text"]


def test_mimo_missing_api_key_fails_fast() -> None:
    llm = MimoAnalysisLLM(api_key="", transport=httpx.MockTransport(None))
    with pytest.raises(AnalysisServiceError, match="MIMO_API_KEY"):
        llm.complete([{"role": "user", "content": "hi"}])


def test_mimo_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []
    monkeypatch.setattr(svc.time, "sleep", lambda d: sleeps.append(d))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                429, headers={"Retry-After": "1.5"}, json={"error": "rate limit"}
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"summary": "重试成功"}'}}]}
        )

    llm = make_llm(handler)
    raw = llm.complete([{"role": "user", "content": "hi"}])
    assert parse_llm_json(raw, SummaryPayload).summary == "重试成功"
    assert len(calls) == 2
    assert sleeps == [1.5]  # Retry-After 优先于指数退避


def test_mimo_4xx_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []
    monkeypatch.setattr(svc.time, "sleep", lambda d: sleeps.append(d))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="invalid api key")

    llm = make_llm(handler, max_retries=2)
    with pytest.raises(AnalysisServiceError, match="HTTP 401"):
        llm.complete([{"role": "user", "content": "hi"}])
    assert len(calls) == 1
    assert sleeps == []


def test_mimo_exhausts_retries_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []
    monkeypatch.setattr(svc.time, "sleep", lambda d: sleeps.append(d))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, text="server error")

    llm = make_llm(handler, max_retries=2, backoff_base=0.5)
    with pytest.raises(AnalysisServiceError, match="已尝试 3 次"):
        llm.complete([{"role": "user", "content": "hi"}])
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]


def test_mimo_empty_content_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "   "}}]}
        )

    llm = make_llm(handler)
    with pytest.raises(AnalysisServiceError, match="空内容"):
        llm.complete([{"role": "user", "content": "hi"}])


def test_mimo_malformed_response_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"foo": "bar"})

    llm = make_llm(handler)
    with pytest.raises(AnalysisServiceError, match="结构异常"):
        llm.complete([{"role": "user", "content": "hi"}])


def test_mimo_timeout_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []
    monkeypatch.setattr(svc.time, "sleep", lambda d: sleeps.append(d))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"summary": "超时后成功"}'}}]}
        )

    llm = make_llm(handler, max_retries=1)
    raw = llm.complete([{"role": "user", "content": "hi"}])
    assert parse_llm_json(raw, SummaryPayload).summary == "超时后成功"
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 关键帧选择与 data URL
# ---------------------------------------------------------------------------


def test_select_keyframes_caps_and_keeps_ends() -> None:
    frames = [Path(f"frame_{i:04d}.jpg") for i in range(120)]  # 长视频 120 帧
    picked = select_keyframes(frames, max_frames=8)
    assert len(picked) == 8
    assert picked[0] == frames[0]      # 首帧
    assert picked[-1] == frames[-1]    # 尾帧
    assert picked == sorted(set(picked), key=frames.index)  # 递增且不重复

    # 不超过上限：原样返回
    assert select_keyframes(frames[:3]) == frames[:3]
    assert select_keyframes([]) == []


def test_image_data_url_formats_and_errors(tmp_path: Path) -> None:
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNGfake")
    assert svc._image_data_url(png).startswith("data:image/png;base64,")

    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    with pytest.raises(AnalysisServiceError, match="为空"):
        svc._image_data_url(empty)

    txt = tmp_path / "a.txt"
    txt.write_bytes(b"x")
    with pytest.raises(AnalysisServiceError, match="不支持"):
        svc._image_data_url(txt)

    with pytest.raises(AnalysisServiceError, match="不存在"):
        svc._image_data_url(tmp_path / "missing.jpg")
