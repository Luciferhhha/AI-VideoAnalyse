"""基础测试配置：保证从项目根目录可导入 app 包 + 共享测试视频 fixture。"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def sample_video() -> Path:
    """生成（或复用）最小测试视频：2 秒，320x240，10fps，h264+aac。"""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURES_DIR / "sample.mp4"
    if out.exists():
        return out
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg, "需要 PATH 中的 ffmpeg 来生成测试视频"
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out),
    ]
    kwargs = {"capture_output": True, "text": True}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(cmd, **kwargs)
    assert result.returncode == 0, f"生成测试视频失败: {result.stderr[-500:]}"
    return out
