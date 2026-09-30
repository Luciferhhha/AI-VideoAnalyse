# 开发记录（development-log）

> 每完成一个阶段在此登记：做了什么、如何验证、遇到的问题、个人确认。
> 只有本人实际运行、测试、理解并确认的功能才记入"个人确认"栏。

---

## 阶段一：项目初始化 — 2026-09-30

### 任务范围

对应 `计划书/详细步骤.md` 阶段一（1.1 ~ 1.10）：创建项目目录、虚拟环境、骨架结构、最小 FastAPI 服务与 `/health` 接口、第一个 pytest、首次 git commit。

### 完成内容

- [x] 1.1 创建 `video-agent/`，`git init`
- [x] 1.2 虚拟环境 `.venv`（Python 3.13.2，位于项目内）
- [x] 1.3 骨架目录：`app/`、`tests/`、`data/{uploads,outputs,database}`、`docs/`、`tools/`
- [x] 1.4 `requirements.txt`（fastapi / uvicorn / pydantic / sqlalchemy / httpx / pytest）
- [x] 1.5 `README.md`（占位）、`.gitignore`、`run.py`
- [x] 1.6 `app/main.py`、`app/config.py`，实现 `/health`
- [x] 1.7 启动服务，`GET /health` 返回 `{"status": "ok"}`（curl + TestClient 各验证一次）
- [x] 1.8 `tests/test_api.py` 全部通过
- [x] 1.9 本开发记录文档创建
- [x] 1.10 commit：`feat: initialize FastAPI project`（commit `9a68773`，14 files changed, 200 insertions）

### 验证结果

- `GET /health`（真实服务，`python run.py` 启动后 curl）：返回 200，`{"status":"ok"}`
- TestClient（`tests/test_api.py`）：`test_health_returns_ok`、`test_health_content_type_json` 均 PASSED
- pytest 汇总：`2 passed, 1 warning in 0.41s`（warning 来自 starlette TestClient 对 httpx 的弃用提示，与业务代码无关）
- 环境：Python 3.13.2（`.venv`）、fastapi 0.142.2、uvicorn 0.54.0、pytest 9.1.1、sqlalchemy 2.1.1

### 遇到的问题与解决

1. FastAPI 0.142 中 `@app.on_event("startup")` 已弃用 → 改用 `lifespan` 上下文管理器，功能不变（确保 `data/` 目录存在）。
2. pip 安装时网络较慢（约 30–70 kB/s），依赖安装耗时较长，属环境问题非代码问题。

### 追加修复（同日，用户实测发现）

**现象**：用户双击 `run.py` 无任何输出；浏览器访问 `http://127.0.0.1:8000/health` 也无响应。

**原因定位**（按"分析→定位→修改→再测试"流程）：
1. `assoc .py` → `.py=Python.File`，`ftype Python.File` → 关联到 `C:\WINDOWS\py.exe`（系统解释器），不是 `.venv` 的 Python；
2. 实测系统 Python `import fastapi` 退出码 1（依赖只装在 `.venv`）→ 双击后抛 ModuleNotFoundError，窗口一闪而过；
3. `Get-NetTCPConnection -LocalPort 8000` → 无监听进程 → 服务根本没起来，浏览器自然打不开。

**修改**：
- `run.py`：启动时检测当前解释器是否为 `.venv\Scripts\python.exe`，不是则自动用 `.venv` 的 Python 重新启动；缺依赖且无 `.venv` 时给出明确安装提示（不再闪退）。
- 新增 `start.bat`：双击即可用 `.venv` 启动，服务停止/出错时窗口 `pause` 保留信息。

**再测试**：
- 用系统 `py.exe run.py`（模拟双击）启动 → `GET /health` 返回 200 `{"status":"ok"}` ✅
- `pytest` 回归：`2 passed` ✅

### 个人确认

（待用户实际运行确认后填写）
