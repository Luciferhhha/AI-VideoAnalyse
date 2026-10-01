"""应用配置。"""

import os
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

# 运行时数据目录
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
OUTPUTS_DIR = DATA_DIR / "outputs"
DATABASE_DIR = DATA_DIR / "database"

# SQLite 数据库文件（posix 路径，避免 Windows 反斜杠在 sqlite URL 中出问题）
DATABASE_URL = f"sqlite:///{(DATABASE_DIR / 'video_agent.db').as_posix()}"

# 上传限制
MAX_UPLOAD_SIZE_MB = 500
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".webm"}

# FFmpeg 可执行文件路径（留空则使用 PATH 中的 ffmpeg/ffprobe）
FFMPEG_DIR = ""

# 语音转文字（阶段七）：mimo / mock / whisper
# - mimo：mimo API 语音识别（第一版默认，需 MIMO_API_KEY）
# - mock：确定性假转写（测试用，无需 API Key）
# - whisper：本地 Whisper 预留接口（第一版不启用，调用即报错）
TRANSCRIPTION_PROVIDER = os.getenv("TRANSCRIPTION_PROVIDER", "mimo")

# mimo API（OpenAI Chat Completion 兼容；填写处见《初版建议》提醒）
MIMO_API_KEY = os.getenv("MIMO_API_KEY", "")
MIMO_BASE_URL = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
MIMO_ASR_MODEL = os.getenv("MIMO_ASR_MODEL", "mimo-v2.5-asr")


def ensure_dirs() -> None:
    """确保运行时目录存在。"""
    for d in (UPLOADS_DIR, OUTPUTS_DIR, DATABASE_DIR):
        d.mkdir(parents=True, exist_ok=True)
