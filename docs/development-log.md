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

### 追加修复二（同日，用户反馈修复后仍闪退）

**现象**：用户双击 `run.py`，cmd 窗口仍然"一闪而过，根本不停留"。

**复现与定位**（按"分析→定位→修改→再测试"流程）：
1. 用文件关联方式（`py.exe`）复现正常启动、关闭后立刻重启 → 均正常，排除 TIME_WAIT/快速重启问题；
2. **关键复现**：先用哑进程占住 8000 端口再启动 → uvicorn 报
   `ERROR: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000): [winerror 10048] 通常每个套接字地址(协议/网络地址/端口)只允许使用一次。`
   随后进程直接退出 → 双击窗口瞬间关闭，且错误信息完全看不到。
   （用户上次启动的服务若未真正退出、或连点两次，就会踩中此场景）
3. 结论：旧版 `run.py` 对"启动失败"没有任何停留/提示机制，任何启动错误都会表现为闪退。

**修改**：
- `run.py` 重写启动流程：① 解释器预检（自动切 `.venv`）→ ② **端口预检**（`netstat` 找占用进程，区分"服务已在运行，直接访问 /health"与"端口被其他进程占用，给出 pid/进程名"）→ ③ 启动 uvicorn；任何异常打印完整 traceback 并**停留等待回车**，同时追加记录到 `logs/launcher.log`（可用 `VIDEO_AGENT_NO_PAUSE=1` 跳过停留）。
- 新增 `tools/launch_probe.py`：启动行为探针，可观测验证三场景（normal / conflict / healthy），stdin 保持打开使"停留"表现为进程存活。
- `.gitignore` 增加 `logs/`。

**再测试**（`tools/launch_probe.py`，均为 exit=0）：
- `normal`：进程存活=True、`/health` 正常=True
- `conflict`：端口被占时进程停留不退出=True、`launcher.log` 记录"端口 8000 已被占用"=True
- `healthy`：服务已运行时二次启动停留并提示=True、原服务仍正常=True
- `pytest` 回归：`2 passed` ✅；测试后 8000 端口干净释放

### 追加修复三（同日，用户反馈 start.bat 闪退）

**现象**：`run.py` 双击已正常（浏览器 `/health` 返回 `{"status":"ok"}`），但双击 `start.bat` 的 cmd 窗口一闪而过。

**定位**：
1. 查 `logs/launcher.log`：用户 run.py 成功启动（21:50:30 `starting server`）之后**再无任何记录** → start.bat 里的 Python 从未被调用，问题在 bat 本身；
2. 查字节：`start.bat` **CRLF=0、全部 bare LF（13 个）**，且为 UTF-8 中文 + 多行 `if/else` 块；
3. 复现（`cmd /c start.bat` 抓输出）：
   ```
   '.venv\Scripts\python.exe" run.py' 不是内部或外部命令
   '建虚拟环境：' 不是内部或外部命令
   '止（出错信息在上方）?pause' 不是内部或外部命令
   ```
   cmd 解析崩坏：引号丢失、`pause` 被并入中文文本行 → 执行不到 `pause` 直接退出 → 闪退。

**修改**：重写 `start.bat` —— 纯 ASCII 文案、`goto` 单行结构（不用多行括号块）、写入后强制转换 CRLF（验证 CRLF=16、bareLF=0、ASCII-only）。

**再测试**（`cmd /c start.bat` + 输出捕获）：
- bat 进程保持运行、8000 端口 LISTENING、`launcher.log` 增长、stderr 出现 `Uvicorn running on http://127.0.0.1:8000` ✅
- `GET /health` → `{"status":"ok"}` ✅；测试后 taskkill 进程树，端口干净释放 ✅

### 个人确认

（待用户实际运行确认后填写）

---

## 阶段二：视频信息解析 — 2026-09-30

### 任务范围

对应 `计划书/详细步骤.md` 阶段二（2.1 ~ 2.4）：ffprobe 元数据提取、三类异常处理、测试视频 fixture 与 pytest 全绿。

### 完成内容

- [x] 2.1 `app/services/video_service.py`：`get_video_info(video_path)`，ffprobe（`-show_format -show_streams` JSON 模式）返回 filename、size、duration、width、height、fps（`r_frame_rate` 用 `fractions.Fraction` 解析）、video_codec、audio_codec
- [x] 2.2 自定义异常层次：`VideoServiceError` 基类 → `VideoNotFoundError` / `InvalidVideoError`（非视频、无视频流、ffprobe 失败/超时/JSON 非法）/ `FFmpegNotFoundError`（定位顺序 `config.FFMPEG_DIR` → `shutil.which`，缺失时给出安装提示）
- [x] 2.3 `tests/test_video.py`：session fixture 用 ffmpeg 现场生成 `tests/fixtures/sample.mp4`（2s，320x240，10fps，h264+aac）；4 条用例覆盖正常、文件不存在、非视频文件、ffprobe 缺失（monkeypatch）
- [x] 2.4 pytest 全绿后 commit（见下）

### 实现要点

- Windows 下 ffprobe 子进程带 `CREATE_NO_WINDOW`，避免控制台窗口闪现。
- ffprobe 走 PATH（本机 `E:\ffmpeg\...\bin` 已在 PATH，`FFMPEG_DIR=""` 即可），未写死路径。

### 验证结果

- `pytest` 全量：**6 passed**（2 个 API + 4 个视频解析），多次运行稳定。
- 正常视频断言：`duration≈2.0`、`320x240`、`fps==10.0`、`h264/aac`，均按 ffprobe 实际输出验证通过。
- 异常路径：不存在 → `VideoNotFoundError`；`.txt` → `InvalidVideoError`；`shutil.which→None` → `FFmpegNotFoundError`，均实测触发。

### 遇到的问题与观察

1. 加入 `test_video.py` 后的**第一次**运行出现过一次 `PytestUnhandledThreadExceptionWarning`（伴 ResourceWarning 提示，输出被截断未抓到完整堆栈）；随后 10 次连续运行（含 `-W error` 模式）均 6 passed、未复现。怀疑为 starlette TestClient/httpx 关闭时的偶发线程竞态（与已知弃用提示同源），**尚未定位到根因，留观**；若再次出现将按流程定位。
2. 测试 fixture `tests/fixtures/sample.mp4` 为可再生产物，加入 `.gitignore`（测试首次运行会自动重建）。

### 个人确认

（待用户实际运行确认后填写）
