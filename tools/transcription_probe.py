"""阶段七真实环境探针：语音转文字链路（提取音轨 → 转写 → 落库 / 失败落原因）。

用法（先启动服务，再另开终端执行其一）：
    # 模式 A：mock 提供方（无需 API Key，验证完整成功链路）
    $env:TRANSCRIPTION_PROVIDER="mock"; python run.py
    .venv\\Scripts\\python.exe tools\\transcription_probe.py mock

    # 模式 B：mimo 提供方但未配置 Key（验证 7.3 失败时任务标记 failed 并记录原因）
    python run.py
    .venv\\Scripts\\python.exe tools\\transcription_probe.py no-key

验证点：
1. POST /videos/{id}/analyze 立即返回 202；
2. mock 模式：任务 → success，AnalysisResult.transcript 为 Mock 转写文本、
   summary 占位记录存在，data/outputs/{id}/audio.wav 真实生成（ffmpeg 提取）；
3. no-key 模式：任务 → failed，error_message 含 MIMO_API_KEY（原因落库），
   且失败后服务仍正常（/health、视频查询 200）；
4. 结束后清理视频/任务/结果/上传文件/输出目录，不污染真实数据。
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8000"
FIXTURE = ROOT / "tests" / "fixtures" / "sample.mp4"
OUTPUTS_DIR = ROOT / "data" / "outputs"
POLL_TIMEOUT = 60.0

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def wait_terminal(client: httpx.Client, task_id: int) -> tuple[str, list[str]]:
    seen: list[str] = []
    deadline = time.monotonic() + POLL_TIMEOUT
    status = "unknown"
    while time.monotonic() < deadline:
        status = client.get(f"/tasks/{task_id}").json()["status"]
        if not seen or seen[-1] != status:
            seen.append(status)
        if status in ("success", "failed"):
            return status, seen
        time.sleep(0.05)
    return status, seen


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("mock", "no-key"):
        print("用法：python tools\\transcription_probe.py <mock|no-key>")
        return 2
    mode = sys.argv[1]

    with httpx.Client(base_url=BASE, timeout=10.0) as client:
        health = client.get("/health")
        check("GET /health", health.status_code == 200 and health.json() == {"status": "ok"})

        up = client.post(
            "/videos",
            files={"file": ("tp.mp4", FIXTURE.read_bytes(), "application/octet-stream")},
        )
        check("POST /videos 上传探针视频", up.status_code == 201, up.text)
        video_id = up.json()["id"]

        t0 = time.monotonic()
        an = client.post(f"/videos/{video_id}/analyze")
        elapsed = time.monotonic() - t0
        check("POST analyze 立即返回 202", an.status_code == 202, an.text)
        check("响应非同步等待", elapsed < 1.0, f"{elapsed:.3f}s")

        task_id = an.json()["task_id"]
        status, seen = wait_terminal(client, task_id)
        detail = client.get(f"/tasks/{task_id}").json()

        if mode == "mock":
            check("任务到达 success", status == "success", f"流转={seen}")
            wav = OUTPUTS_DIR / str(video_id) / "audio.wav"
            check(
                "真实生成提取音轨 data/outputs/{id}/audio.wav",
                wav.is_file() and wav.stat().st_size > 0,
                f"{wav}（{wav.stat().st_size if wav.exists() else 0} bytes）",
            )
            from app.database.database import SessionLocal
            from app.database.repository import ResultRepository

            with SessionLocal() as session:
                result = ResultRepository(session).get_by_task(task_id)
            check(
                "AnalysisResult.transcript 为 Mock 转写文本",
                result is not None
                and result.transcript == "这是一段由 Mock 转写服务生成的示例文本，用于无 API Key 的测试。",
                (result.transcript if result else "无记录"),
            )
            check(
                "summary 占位记录存在",
                result is not None and bool(result.summary),
                (result.summary if result else "无记录"),
            )
        else:  # no-key
            check(
                "未配置 Key → 任务 failed 且记录原因",
                status == "failed" and "MIMO_API_KEY" in (detail.get("error_message") or ""),
                f"流转={seen} error={detail.get('error_message')}",
            )
            check(
                "失败后服务仍正常（/health、视频查询）",
                client.get("/health").status_code == 200
                and client.get(f"/videos/{video_id}").status_code == 200,
            )

        # 清理：级联删除视频任务结果 + 上传文件 + 输出目录
        from app.database.database import SessionLocal
        from app.database.repository import VideoRepository

        with SessionLocal() as session:
            deleted = VideoRepository(session).delete(video_id)
        for path in (ROOT / "data" / "uploads").glob("*_tp.mp4"):
            path.unlink(missing_ok=True)
        shutil.rmtree(OUTPUTS_DIR / str(video_id), ignore_errors=True)
        leftover = list((ROOT / "data" / "uploads").glob("*_tp.mp4"))
        check(
            "清理探针数据（视频级联删除 + 上传文件 + 输出目录）",
            deleted and not leftover and not (OUTPUTS_DIR / str(video_id)).exists(),
            f"deleted={deleted} leftover={[p.name for p in leftover]}",
        )

    print()
    if _failures:
        print(f"探针失败 {len(_failures)} 项：{_failures}")
        return 1
    print(f"探针全部通过（mode={mode}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
