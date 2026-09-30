"""启动行为探针：验证 run.py 在三种场景下的表现。

场景（每个子命令对应一次验证）：
- normal   端口空闲 → 应正常启动并响应 /health
- conflict 端口被无关进程占用 → 应停留并提示"端口被占用"（不闪退）
- healthy  服务已在运行时再次启动 → 应停留并提示"已在运行"

原理：用 `py run.py`（与双击一致的关联方式）启动，stdin 接管道并保持打开，
使 run.py 失败时的 input() 停留可以被观测为"进程仍然存活"。

用法：
  .venv\\Scripts\\python tools\\launch_probe.py normal
  .venv\\Scripts\\python tools\\launch_probe.py conflict
  .venv\\Scripts\\python tools\\launch_probe.py healthy
"""

import http.client
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_PY = str(ROOT / "run.py")
LAUNCH_LOG = ROOT / "logs" / "launcher.log"
PY = shutil.which("py") or "C:\\WINDOWS\\py.exe"
HOST, PORT = "127.0.0.1", 8000


def health_ok() -> bool:
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=1)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status == 200 and body.get("status") == "ok"
    except (OSError, ValueError):
        return False


def launch() -> subprocess.Popen:
    """按双击的方式（py.exe + 文件关联语义）启动 run.py，保持 stdin 打开。"""
    return subprocess.Popen(
        [PY, RUN_PY],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)


def wait_port_free(timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.3)
            try:
                s.connect((HOST, PORT))
            except OSError:
                return True
        time.sleep(0.3)
    return False


def scenario_normal() -> int:
    if not wait_port_free():
        print("SKIP: 端口 8000 被占用，先清理")
        return 1
    p = launch()
    time.sleep(6)
    alive, healthy = p.poll() is None, health_ok()
    print(f"[normal] 进程存活={alive} /health正常={healthy}")
    kill_tree(p.pid)
    time.sleep(1)
    return 0 if alive and healthy else 1


def scenario_conflict() -> int:
    blocker = socket.socket()
    blocker.bind((HOST, PORT))
    blocker.listen(1)
    try:
        p = launch()
        time.sleep(5)
        alive = p.poll() is None  # 期望：停留等待回车，而不是退出
        log_text = LAUNCH_LOG.read_text(encoding="utf-8") if LAUNCH_LOG.exists() else ""
        logged = "端口 8000 已被占用" in log_text
        print(f"[conflict] 进程仍存活(停留)={alive} 日志记录原因={logged}")
        kill_tree(p.pid)
        return 0 if alive and logged else 1
    finally:
        blocker.close()


def scenario_healthy() -> int:
    if not wait_port_free():
        print("SKIP: 端口 8000 被占用，先清理")
        return 1
    server = launch()
    for _ in range(30):
        if health_ok():
            break
        time.sleep(0.5)
    if not health_ok():
        kill_tree(server.pid)
        print("[healthy] 首次启动失败")
        return 1
    second = launch()
    time.sleep(5)
    alive = second.poll() is None
    log_text = LAUNCH_LOG.read_text(encoding="utf-8") if LAUNCH_LOG.exists() else ""
    logged = "服务已经在运行" in log_text
    still_healthy = health_ok()
    print(f"[healthy] 二次启动停留={alive} 日志提示已在运行={logged} 原服务仍正常={still_healthy}")
    kill_tree(second.pid)
    kill_tree(server.pid)
    return 0 if alive and logged and still_healthy else 1


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"normal", "conflict", "healthy"}:
        print(__doc__)
        return 2
    return {"normal": scenario_normal, "conflict": scenario_conflict, "healthy": scenario_healthy}[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
