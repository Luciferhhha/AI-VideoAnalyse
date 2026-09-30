"""阶段五真实环境探针：验证异步分析任务在真实 uvicorn 下的行为。

用法（先启动服务 `python run.py`，再另开终端执行）：
    .venv\\Scripts\\python.exe tools\\task_probe.py

验证点（对应 5.1~5.4）：
1. POST /videos/{id}/analyze 立即返回 task_id（不等待后台完成）；
2. 后台任务真实异步执行：轮询 GET /tasks/{id} 能观察到状态流转直到 success；
3. success 任务在库中有对应的 AnalysisResult 占位记录；
4. 失败路径：删除视频文件后再发起分析 → failed + error_message，服务不崩溃；
5. 结束后自动清理本次探针创建的视频/任务/文件，不污染真实数据。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8000"
FIXTURE = ROOT / "tests" / "fixtures" / "sample.mp4"
POLL_INTERVAL = 0.05
POLL_TIMEOUT = 30.0

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def wait_terminal(client: httpx.Client, task_id: int) -> tuple[str, list[str]]:
    """轮询任务状态直到终态，返回 (终态, 观察到的状态序列)。"""
    seen: list[str] = []
    deadline = time.monotonic() + POLL_TIMEOUT
    status = "unknown"
    while time.monotonic() < deadline:
        status = client.get(f"/tasks/{task_id}").json()["status"]
        if not seen or seen[-1] != status:
            seen.append(status)
        if status in ("success", "failed"):
            return status, seen
        time.sleep(POLL_INTERVAL)
    return status, seen


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=10.0) as client:
        # 0) 服务存活
        health = client.get("/health")
        check("GET /health", health.status_code == 200 and health.json() == {"status": "ok"})

        # 1) 上传探针视频
        up = client.post(
            "/videos",
            files={"file": ("probe.mp4", FIXTURE.read_bytes(), "application/octet-stream")},
        )
        check("POST /videos 上传探针视频", up.status_code == 201, up.text)
        video_id = up.json()["id"]
        video_filepath = _filepath_of(video_id)

        # 2) 发起分析：立即返回
        t0 = time.monotonic()
        an = client.post(f"/videos/{video_id}/analyze")
        elapsed = time.monotonic() - t0
        check("POST analyze 立即返回 202", an.status_code == 202, an.text)
        body = an.json()
        check(
            "返回 task_id 且创建瞬间为 pending",
            body.get("status") == "pending" and isinstance(body.get("task_id"), int),
            str(body),
        )
        check("响应耗时远小于轮询周期级（非同步等待）", elapsed < 1.0, f"{elapsed:.3f}s")

        # 3) 后台异步执行 → 轮询到终态
        task_id = body["task_id"]
        status, seen = wait_terminal(client, task_id)
        check("后台任务到达 success", status == "success", f"观察到的状态流转：{seen}")

        # 4) success 任务有占位 AnalysisResult
        from app.database.database import SessionLocal
        from app.database.repository import ResultRepository, VideoRepository

        with SessionLocal() as session:
            result = ResultRepository(session).get_by_task(task_id)
            check(
                "success 任务写入占位 AnalysisResult",
                result is not None and bool(result.summary),
                (result.summary if result else "无记录"),
            )

        # 5) 失败路径：删文件后再分析 → failed + error_message，服务不崩溃
        path = Path(video_filepath)
        path.unlink(missing_ok=True)
        an2 = client.post(f"/videos/{video_id}/analyze")
        task_id2 = an2.json()["task_id"]
        status2, seen2 = wait_terminal(client, task_id2)
        detail = client.get(f"/tasks/{task_id2}").json()
        check(
            "文件缺失 → failed 且带 error_message",
            status2 == "failed" and "视频文件不存在" in (detail.get("error_message") or ""),
            f"流转={seen2} error={detail.get('error_message')}",
        )
        check(
            "失败后服务仍正常（/health、视频查询）",
            client.get("/health").status_code == 200
            and client.get(f"/videos/{video_id}").status_code == 200,
        )

        # 6) 404 路径
        check(
            "不存在的视频 → 404",
            client.post("/videos/999999/analyze").status_code == 404,
        )
        check("不存在的任务 → 404", client.get("/tasks/999999").status_code == 404)

        # 7) 清理本次探针数据（级联删除任务与结果）
        with SessionLocal() as session:
            deleted = VideoRepository(session).delete(video_id)
        leftover = list((ROOT / "data" / "uploads").glob("*_probe.mp4"))
        check(
            "清理探针数据（视频级联删除 + 文件删除）",
            deleted and not leftover,
            f"deleted={deleted} leftover={[p.name for p in leftover]}",
        )

    print()
    if _failures:
        print(f"探针失败 {len(_failures)} 项：{_failures}")
        return 1
    print("探针全部通过")
    return 0


def _filepath_of(video_id: int) -> str:
    """从数据库读取视频文件路径（探针内部用）。"""
    from app.database.database import SessionLocal
    from app.database.repository import VideoRepository

    with SessionLocal() as session:
        video = VideoRepository(session).get(video_id)
        assert video is not None
        return video.filepath


if __name__ == "__main__":
    raise SystemExit(main())
