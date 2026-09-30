"""启动入口。

用法：
- 双击本文件或 start.bat；或在终端执行 `python run.py`
- 当前解释器不是项目 .venv 时，自动改用 .venv 启动。
- 启动失败时窗口会停留并显示原因，同时追加记录到 logs/launcher.log，
  避免"一闪而过"无从排查。（设置环境变量 VIDEO_AGENT_NO_PAUSE=1 可跳过停留）
"""

import http.client
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
LOG_DIR = BASE_DIR / "logs"
LAUNCH_LOG = LOG_DIR / "launcher.log"
HOST = "127.0.0.1"
PORT = 8000


def _log(message: str) -> None:
    """追加一行启动记录到 logs/launcher.log（失败不影响启动）。"""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with LAUNCH_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [pid {os.getpid()}] {message}\n")
    except OSError:
        pass


def _pause(reason: str) -> None:
    """失败时打印原因并让窗口停留（等待回车），不做静默闪退。"""
    print(reason)
    _log(reason.replace("\n", " | "))
    if os.environ.get("VIDEO_AGENT_NO_PAUSE"):
        return
    try:
        input("按回车关闭窗口 ...")
    except (EOFError, OSError, KeyboardInterrupt):
        pass


def _find_port_owner(port: int) -> tuple[int | None, str]:
    """用 netstat 查找处于 LISTENING 的端口占用进程，返回 (pid, 进程名)。"""
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=no_window,
        )
    except OSError:
        return None, ""
    suffix = f":{port}"
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(suffix) and "LISTENING" in line:
            try:
                pid = int(parts[-1])
            except ValueError:
                return None, ""
            name = ""
            try:
                t = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    creationflags=no_window,
                )
                if '"' in t.stdout:
                    name = t.stdout.strip().split('"')[1]
            except OSError:
                pass
            return pid, name
    return None, ""


def _service_already_healthy() -> bool:
    """探测 127.0.0.1:8000/health 是否已经是本服务在响应。"""
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=1)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status == 200 and body.get("status") == "ok"
    except (OSError, ValueError):
        return False


def _in_project_venv() -> bool:
    """判断当前解释器是否就是项目 .venv 里的 Python。"""
    if not VENV_PYTHON.exists():
        return False
    try:
        return Path(sys.executable).resolve() == VENV_PYTHON.resolve()
    except OSError:
        return False


def _run_server() -> int:
    # 1) 解释器检查：双击时是系统 Python，自动切到 .venv
    if not _in_project_venv():
        if VENV_PYTHON.exists():
            print(f"[run.py] 当前解释器不是 .venv，改用 {VENV_PYTHON} 启动 ...")
            return subprocess.run([str(VENV_PYTHON), str(BASE_DIR / "run.py")], cwd=BASE_DIR).returncode
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError:
            _pause(
                "错误：当前 Python 缺少依赖，且未找到 .venv。\n"
                "请先创建虚拟环境并安装依赖：\n"
                "  python -m venv .venv\n"
                '  .venv\\Scripts\\python -m pip install -r requirements.txt'
            )
            return 1

    # 2) 端口预检：被占用时不启动，直接给出明确原因（避免 uvicorn 报错后闪退）
    pid, name = _find_port_owner(PORT)
    if pid is not None:
        if _service_already_healthy():
            _pause(
                f"服务已经在运行了（端口 {PORT} 由 {name} / pid {pid} 监听）。\n"
                f"无需重复启动，直接在浏览器打开 http://{HOST}:{PORT}/health 即可。"
            )
            return 0
        _pause(
            f"启动失败：端口 {PORT} 已被占用。\n"
            f"占用进程：{name or '未知进程'}（pid {pid}）\n"
            "请先结束该进程（或改用其他端口），再重新启动。"
        )
        return 1

    # 3) 启动服务
    from app.main import app  # 延迟导入：确保依赖已就绪

    import uvicorn

    print(f"[run.py] 启动服务：http://{HOST}:{PORT}  （健康检查：/health）")
    _log(f"starting server on {HOST}:{PORT} exe={sys.executable}")
    uvicorn.run(app, host=HOST, port=PORT)
    _log("server exited normally")
    return 0


def main() -> int:
    _log(f"launch exe={sys.executable} argv0={sys.argv[0]}")
    try:
        return _run_server()
    except KeyboardInterrupt:
        _log("interrupted by user")
        print("\n已中断，服务停止。")
        return 0
    except BaseException:
        detail = traceback.format_exc()
        print(detail)
        _pause("启动失败（完整错误见上方，已记录到 logs\\launcher.log）")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
