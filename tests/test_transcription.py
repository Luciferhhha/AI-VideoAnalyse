"""阶段七测试：语音转文字服务（7.1~7.5）。

全部走 Mock 路径（httpx.MockTransport + 确定性 Mock 服务），不依赖网络与
真实 API Key（7.5）。覆盖：输出契约、data URL 请求构造、退避重试
（429/Retry-After、5xx、网络错误、超时）、非重试失败（401/坏 JSON/空文本）、
whisper 预留、工厂切换、时长降级。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.services import transcription_service as ts
from app.services.transcription_service import (
    MimoTranscriptionService,
    MockTranscriptionService,
    TranscriptionError,
    WhisperTranscriptionService,
    get_transcription_service,
)


def make_wav(path: Path, seconds: float = 0.1, rate: int = 16000) -> Path:
    """生成 16kHz 单声道 16-bit wav（与阶段六产物同规格）。"""
    import wave

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def asr_ok(text: str = "你好，世界", seconds: float = 3) -> httpx.Response:
    """构造官方 ASR 200 响应（choices[0].message.content 纯字符串 + usage.seconds）。"""
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": text}}], "usage": {"seconds": seconds}},
    )


def make_service(handler, **kwargs) -> MimoTranscriptionService:
    kwargs.setdefault("api_key", "test-key")
    kwargs.setdefault("transport", httpx.MockTransport(handler))
    return MimoTranscriptionService(**kwargs)


def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """把重试退避的 sleep 替换为记录（不真的等待）。"""
    slept: list[float] = []
    monkeypatch.setattr(ts.time, "sleep", lambda s: slept.append(s))
    return slept


# ---------------------------------------------------------------- Mock 服务


def test_mock_transcribe_returns_contract(tmp_path: Path) -> None:
    wav = make_wav(tmp_path / "a.wav", seconds=0.25)

    out = MockTranscriptionService().transcribe(wav)

    assert out["text"] == MockTranscriptionService.MOCK_TEXT
    assert len(out["segments"]) == 1
    seg = out["segments"][0]
    assert seg["start"] == 0.0
    assert abs(seg["end"] - 0.25) < 1e-6
    assert seg["text"] == out["text"]


def test_mock_transcribe_missing_file(tmp_path: Path) -> None:
    with pytest.raises(TranscriptionError, match="音频文件不存在"):
        MockTranscriptionService().transcribe(tmp_path / "nope.wav")


# ---------------------------------------------------------------- 工厂切换


def test_factory_defaults_to_mimo(monkeypatch) -> None:
    monkeypatch.setattr(ts.config, "TRANSCRIPTION_PROVIDER", "mimo")

    svc = get_transcription_service()

    assert isinstance(svc, MimoTranscriptionService)
    assert svc.model == "mimo-v2.5-asr"
    assert svc.base_url == "https://api.xiaomimimo.com/v1"


def test_factory_switch_to_mock(monkeypatch) -> None:
    monkeypatch.setattr(ts.config, "TRANSCRIPTION_PROVIDER", "mock")
    assert isinstance(get_transcription_service(), MockTranscriptionService)


def test_factory_whisper_reserved_but_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ts.config, "TRANSCRIPTION_PROVIDER", "whisper")
    svc = get_transcription_service()
    assert isinstance(svc, WhisperTranscriptionService)
    with pytest.raises(TranscriptionError, match="不启用"):
        svc.transcribe(make_wav(tmp_path / "a.wav"))


def test_factory_unknown_provider(monkeypatch) -> None:
    monkeypatch.setattr(ts.config, "TRANSCRIPTION_PROVIDER", "deepl")
    with pytest.raises(TranscriptionError, match="未知的转写服务提供方"):
        get_transcription_service()


# ---------------------------------------------------------------- mimo 成功路径


def test_mimo_success_request_and_contract(tmp_path: Path, monkeypatch) -> None:
    wav = make_wav(tmp_path / "a.wav", seconds=0.1)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["payload"] = json.loads(request.content)
        return asr_ok("转写出的文本")

    svc = make_service(handler)
    out = svc.transcribe(wav)

    # 请求构造：端点 / 鉴权 / 模型 / data URL 音频输入
    assert seen["url"] == "https://api.xiaomimimo.com/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    payload = seen["payload"]
    assert payload["model"] == "mimo-v2.5-asr"
    part = payload["messages"][0]["content"][0]
    assert part["type"] == "input_audio"
    assert part["input_audio"]["data"].startswith("data:audio/wav;base64,")

    # 输出契约：单段降级，时长取 wav 实际时长（优先于 usage.seconds=3）
    assert out["text"] == "转写出的文本"
    assert len(out["segments"]) == 1
    assert out["segments"][0]["start"] == 0.0
    assert abs(out["segments"][0]["end"] - 0.1) < 1e-6
    assert out["segments"][0]["text"] == "转写出的文本"


def test_mimo_missing_api_key_fails_before_request(tmp_path: Path) -> None:
    wav = make_wav(tmp_path / "a.wav")
    called = []

    svc = make_service(lambda r: called.append(r) or asr_ok(), api_key="")

    with pytest.raises(TranscriptionError, match="MIMO_API_KEY"):
        svc.transcribe(wav)
    assert called == [], "缺 Key 时不应发出任何请求"


def test_mimo_missing_audio_file(tmp_path: Path) -> None:
    svc = make_service(lambda r: asr_ok())
    with pytest.raises(TranscriptionError, match="音频文件不存在"):
        svc.transcribe(tmp_path / "nope.wav")


def test_mimo_duration_falls_back_to_usage_seconds(tmp_path: Path) -> None:
    # 非 wav（mp3）读不到时长 → 降级用响应 usage.seconds
    fake_mp3 = tmp_path / "a.mp3"
    fake_mp3.write_bytes(b"ID3 fake mp3 bytes")
    svc = make_service(lambda r: asr_ok("hello", seconds=7))

    out = svc.transcribe(fake_mp3)

    assert abs(out["segments"][0]["end"] - 7.0) < 1e-6


def test_mimo_duration_zero_when_unknown(tmp_path: Path) -> None:
    fake_mp3 = tmp_path / "a.mp3"
    fake_mp3.write_bytes(b"ID3 fake mp3 bytes")
    svc = make_service(lambda r: asr_ok("hello", seconds=0))

    out = svc.transcribe(fake_mp3)

    assert out["segments"][0]["end"] == 0.0


# ---------------------------------------------------------------- 重试与失败（7.3）


def test_429_respects_retry_after_then_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    wav = make_wav(tmp_path / "a.wav")
    slept = no_sleep(monkeypatch)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "1.5"}, text="rate limited")
        return asr_ok("重试成功")

    out = make_service(handler).transcribe(wav)

    assert len(attempts) == 2
    assert slept == [1.5], "429 应按 Retry-After 退避"
    assert out["text"] == "重试成功"


def test_5xx_exhausts_retries_then_fails(tmp_path: Path, monkeypatch) -> None:
    wav = make_wav(tmp_path / "a.wav")
    slept = no_sleep(monkeypatch)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500, text="internal server error")

    svc = make_service(handler, max_retries=2)
    with pytest.raises(TranscriptionError) as exc:
        svc.transcribe(wav)

    assert len(attempts) == 3, "max_retries=2 → 最多 3 次尝试"
    assert "已尝试 3 次" in str(exc.value)
    assert "HTTP 500" in str(exc.value)
    assert len(slept) == 2, "每次重试前退避一次"


def test_401_fails_immediately_without_retry(tmp_path: Path, monkeypatch) -> None:
    wav = make_wav(tmp_path / "a.wav")
    slept = no_sleep(monkeypatch)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(401, text="invalid api key")

    with pytest.raises(TranscriptionError, match="HTTP 401"):
        make_service(handler).transcribe(wav)

    assert len(attempts) == 1, "4xx 确定性失败不重试"
    assert slept == []


def test_network_error_retries_then_fails(tmp_path: Path, monkeypatch) -> None:
    wav = make_wav(tmp_path / "a.wav")
    slept = no_sleep(monkeypatch)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("connection refused")

    with pytest.raises(TranscriptionError, match="网络错误"):
        make_service(handler, max_retries=1).transcribe(wav)

    assert len(attempts) == 2
    assert len(slept) == 1


def test_timeout_retries_then_fails(tmp_path: Path, monkeypatch) -> None:
    wav = make_wav(tmp_path / "a.wav")
    slept = no_sleep(monkeypatch)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ReadTimeout("read timed out")

    with pytest.raises(TranscriptionError, match="请求超时"):
        make_service(handler, max_retries=1, timeout=0.5).transcribe(wav)

    assert len(attempts) == 2


def test_bad_json_fails_without_retry(tmp_path: Path) -> None:
    wav = make_wav(tmp_path / "a.wav")
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(TranscriptionError, match="不是合法 JSON"):
        make_service(handler).transcribe(wav)

    assert len(attempts) == 1


def test_empty_text_fails(tmp_path: Path) -> None:
    wav = make_wav(tmp_path / "a.wav")
    with pytest.raises(TranscriptionError, match="空文本"):
        make_service(lambda r: asr_ok("   ")).transcribe(wav)


def test_malformed_response_shape_fails(tmp_path: Path) -> None:
    wav = make_wav(tmp_path / "a.wav")
    bad = httpx.Response(200, json={"choices": []})
    with pytest.raises(TranscriptionError, match="结构异常"):
        make_service(lambda r: bad).transcribe(wav)


def test_unsupported_audio_format(tmp_path: Path) -> None:
    bogus = tmp_path / "a.ogg"
    bogus.write_bytes(b"ogg-bytes")
    with pytest.raises(TranscriptionError, match="仅支持 wav/mp3"):
        make_service(lambda r: asr_ok()).transcribe(bogus)
