"""语音转文字服务（阶段七）：统一接口 TranscriptionService + mimo API + Mock。

计划书第十章 7.1~7.5：
- 7.1 统一接口 `TranscriptionService`，可按 `config.TRANSCRIPTION_PROVIDER`
  在 mimo（API）/ mock（测试）/ whisper（本地预留）之间切换；
- 7.2 第一版实现 mimo API 音频输入方案（mimo-v2.5-asr，OpenAI Chat
  Completion 兼容，见《初版建议》）；
- 7.3 超时、限流（429）退避重试与降级；失败抛 `TranscriptionError`，
  由任务链路落 `task.error_message`（失败时任务标记 failed 并记录原因）；
- 7.4 Mock 实现供测试使用（确定性输出，无 API Key 也能全绿）。

输出契约（JSON 可序列化）：
    {"text": str, "segments": [{"start": float, "end": float, "text": str}]}

降级说明：官方 ASR 响应 `choices[0].message.content` 是纯字符串、**不带
时间戳**，因此 segments 降级为单段：优先用 wav 实际时长（阶段六产物为
16kHz 单声道 wav，可用 stdlib wave 精确读取），否则取响应 `usage.seconds`，
再否则 0.0。
"""

from __future__ import annotations

import base64
import logging
import time
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

from app import config

logger = logging.getLogger(__name__)

# 重试策略：429/5xx/网络错误/超时 可重试（官方 rate-limit 文档建议实现退避重试）；
# 401/400 等其余 4xx 属确定性失败，不重试。退避延迟有上限，Retry-After 优先。
_MAX_BACKOFF_SECONDS = 30.0


class TranscriptionError(Exception):
    """转写失败（原因写入 task.error_message）。"""


class TranscriptionService(ABC):
    """语音转文字统一接口（Whisper / API / Mock 可切换）。"""

    @abstractmethod
    def transcribe(self, audio_path: str | Path) -> dict[str, Any]:
        """转写一个音频文件，返回 {"text", "segments"} 契约；失败抛 TranscriptionError。"""


def wav_duration_seconds(path: str | Path) -> float | None:
    """用 stdlib wave 读取 wav 时长（秒）；非 wav 或损坏返回 None。"""
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            if rate <= 0:
                return None
            return wf.getnframes() / rate
    except (wave.Error, OSError, EOFError):
        return None


def _encode_data_url(path: Path) -> str:
    """把音频文件编码为 data URL（wav→audio/wav，mp3→audio/mpeg）。"""
    suffix = path.suffix.lower()
    mime = {".wav": "audio/wav", ".mp3": "audio/mpeg"}.get(suffix)
    if mime is None:
        raise TranscriptionError(
            f"不支持的音频格式（mimo 转写仅支持 wav/mp3）：{path.name}"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TranscriptionError(f"读取音频文件失败：{path} —— {exc}") from exc
    if not raw:
        raise TranscriptionError(f"音频文件为空：{path.name}")
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


class MimoTranscriptionService(TranscriptionService):
    """mimo API 语音识别（mimo-v2.5-asr，data URL 音频输入）。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.xiaomimimo.com/v1",
        model: str = "mimo-v2.5-asr",
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

    def transcribe(self, audio_path: str | Path) -> dict[str, Any]:
        path = Path(audio_path)
        if not path.is_file():
            raise TranscriptionError(f"音频文件不存在：{path}")
        if not self.api_key:
            # 框架先行（《初版建议》）：提示用户填写 API Key
            raise TranscriptionError(
                "未配置 MIMO_API_KEY（环境变量或 app/config.py），无法调用 mimo 转写 API。"
            )
        data_url = _encode_data_url(path)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    # data URL 形式时 format 选填，省略以避免与 MIME 不一致
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": data_url}}
                    ],
                }
            ],
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
                        return self._parse_response(resp, path)
                    detail = (resp.text or "").strip().replace("\n", " ")[-300:]
                    if resp.status_code == 429 or resp.status_code >= 500:
                        last_error = f"HTTP {resp.status_code}：{detail}"
                        retry_after = resp.headers.get("Retry-After")
                    else:
                        # 401/400 等：确定性失败，不重试
                        raise TranscriptionError(
                            f"mimo 转写请求被拒绝（HTTP {resp.status_code}）：{detail}"
                        )
                if attempt >= self.max_retries:
                    break
                delay = self._backoff_delay(attempt, retry_after)
                logger.warning(
                    "mimo 转写第 %d 次尝试失败，%.1fs 后重试：%s",
                    attempt + 1, delay, last_error,
                )
                time.sleep(delay)

        raise TranscriptionError(
            f"mimo 转写失败（已尝试 {self.max_retries + 1} 次）：{last_error}"
        )

    def _backoff_delay(self, attempt: int, retry_after: str | None) -> float:
        """指数退避；响应带 Retry-After 时优先采用（封顶 _MAX_BACKOFF_SECONDS)。"""
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass  # HTTP-date 等格式不解析，退回指数退避
        return min(self.backoff_base * (2**attempt), _MAX_BACKOFF_SECONDS)

    def _parse_response(self, resp: httpx.Response, path: Path) -> dict[str, Any]:
        """解析 chat.completion 响应 → 输出契约；segments 按时长降级为单段。"""
        try:
            data = resp.json()
        except ValueError as exc:
            raise TranscriptionError(
                f"mimo 转写响应不是合法 JSON：{(resp.text or '')[:200]}"
            ) from exc
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TranscriptionError(
                "mimo 转写响应结构异常：缺少 choices[0].message.content"
            ) from exc
        if not isinstance(text, str) or not text.strip():
            raise TranscriptionError("mimo 转写返回空文本")

        # 时长来源优先级：wav 实际时长 → usage.seconds → 0.0
        duration = wav_duration_seconds(path) or 0.0
        if duration <= 0:
            usage = data.get("usage") or {}
            seconds = usage.get("seconds")
            if isinstance(seconds, (int, float)) and seconds > 0:
                duration = float(seconds)
        segments = [{"start": 0.0, "end": duration, "text": text}]
        return {"text": text, "segments": segments}


class MockTranscriptionService(TranscriptionService):
    """确定性假转写（7.4）：无需 API Key，读 wav 时长生成契约输出。"""

    MOCK_TEXT = "这是一段由 Mock 转写服务生成的示例文本，用于无 API Key 的测试。"

    def transcribe(self, audio_path: str | Path) -> dict[str, Any]:
        path = Path(audio_path)
        if not path.is_file():
            raise TranscriptionError(f"音频文件不存在：{path}")
        duration = wav_duration_seconds(path) or 0.0
        return {
            "text": self.MOCK_TEXT,
            "segments": [{"start": 0.0, "end": duration, "text": self.MOCK_TEXT}],
        }


class WhisperTranscriptionService(TranscriptionService):
    """本地 Whisper 预留接口（7.1）：第一版不启用（《初版建议》全走 mimo API）。"""

    def transcribe(self, audio_path: str | Path) -> dict[str, Any]:
        raise TranscriptionError(
            "Whisper 本地转写为预留接口，第一版不启用（计划书：第一版不使用本地 Whisper，"
            "请把 TRANSCRIPTION_PROVIDER 设为 mimo 并配置 MIMO_API_KEY）。"
        )


def get_transcription_service() -> TranscriptionService:
    """按 config.TRANSCRIPTION_PROVIDER 构建转写服务（每次调用读配置，便于切换）。"""
    provider = (config.TRANSCRIPTION_PROVIDER or "mimo").strip().lower()
    if provider == "mimo":
        return MimoTranscriptionService(
            api_key=config.MIMO_API_KEY,
            base_url=config.MIMO_BASE_URL,
            model=config.MIMO_ASR_MODEL,
        )
    if provider == "mock":
        return MockTranscriptionService()
    if provider == "whisper":
        return WhisperTranscriptionService()
    raise TranscriptionError(
        f"未知的转写服务提供方：{config.TRANSCRIPTION_PROVIDER!r}（可选 mimo / mock / whisper）"
    )
