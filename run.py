"""启动入口。

用法：
- 双击本文件（或 start.bat），或在终端执行 `python run.py`
- 如果当前 Python 不是项目 .venv 里的解释器，会自动用 .venv 重新启动服务，
  避免出现 "ModuleNotFoundError: No module named 'fastapi'" 一闪而过的问题。
"""

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"


def _in_project_venv() -> bool:
    """判断当前解释器是否就是项目 .venv 里的 Python。"""
    if not VENV_PYTHON.exists():
        return False
    try:
        return Path(sys.executable).resolve() == VENV_PYTHON.resolve()
    except OSError:
        return False


def main() -> int:
    if not _in_project_venv():
        if VENV_PYTHON.exists():
            print(f"[run.py] 当前解释器不是 .venv，改用 {VENV_PYTHON} 启动 ...")
            return subprocess.run([str(VENV_PYTHON), str(BASE_DIR / "run.py")], cwd=BASE_DIR).returncode
        # 没有虚拟环境：尝试原地导入，失败则给出明确提示
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError:
            print("错误：当前 Python 缺少依赖，且未找到 .venv。")
            print("请先创建虚拟环境并安装依赖：")
            print("  python -m venv .venv")
            print("  .venv\\Scripts\\python -m pip install -r requirements.txt")
            return 1

    from app.main import app  # 延迟导入：确保依赖已就绪

    import uvicorn

    print("[run.py] 启动服务：http://127.0.0.1:8000  （健康检查：/health）")
    uvicorn.run(app, host="127.0.0.1", port=8000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
