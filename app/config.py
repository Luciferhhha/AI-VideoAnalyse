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

# AI 内容分析（阶段九）：mimo / mock
# - mimo：mimo API 多模态（转写文本 + 关键帧图像输入，默认，需 MIMO_API_KEY）
# - mock：MockLLM 固定 JSON（测试用，无需 API Key）
ANALYSIS_PROVIDER = os.getenv("ANALYSIS_PROVIDER", "mimo")
# 视觉模型：官方支持 mimo-v2.6-flash / mimo-v2.6-pro / mimo-v2.6-pro-ultraspeed / mimo-v2.5
MIMO_ANALYSIS_MODEL = os.getenv("MIMO_ANALYSIS_MODEL", "mimo-v2.6-flash")

# Video Analysis Agent（阶段十）：mimo / mock
# - mimo：mimo 原生 Tool Calling（默认，需 MIMO_API_KEY）
# - mock：脚本化 Tool 序列（测试用，无需 API Key）
AGENT_PROVIDER = os.getenv("AGENT_PROVIDER", "mimo")
MIMO_AGENT_MODEL = os.getenv("MIMO_AGENT_MODEL", "mimo-v2.6-flash")

# 分析任务驱动方式（10.6）：agent / direct
# - agent：Agent 循环驱动 8 个 Tool 完成分析（默认，项目核心链路）
# - direct：阶段九的直连链路（保留作对照与降级）
AGENT_DRIVER = os.getenv("AGENT_DRIVER", "agent")


def ensure_dirs() -> None:
    """确保运行时目录存在。"""
    for d in (UPLOADS_DIR, OUTPUTS_DIR, DATABASE_DIR):
        d.mkdir(parents=True, exist_ok=True)
