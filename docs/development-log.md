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

---

## 阶段三：数据库设计 — 2026-09-30

### 任务范围

对应 `计划书/详细步骤.md` 阶段三（3.1 ~ 3.5）：SQLite + SQLAlchemy 模型、引擎/会话/Base、Repository 层、初始化与 CRUD、pytest。

字段与状态机严格按计划书《视频分析项目.docx》"六、第三阶段"：
- `Video`：id, filename, filepath, duration, width, height, fps, created_at
- `AnalysisTask`：id, video_id, status, created_at, started_at, finished_at, error_message
- `AnalysisResult`：id, task_id, summary, keywords, chapters, transcript, created_at
- 状态机：pending / running / success / failed

### 完成内容

- [x] 3.1 `app/database/models.py`：三个模型（SQLAlchemy 2.x `Mapped` 风格）；`TaskStatus` 常量；`analysis_tasks.status` 加 CHECK 约束；级联删除（删 Video → 连带 Task → Result）
- [x] 3.2 `app/database/database.py`（`Base`、`create_db_engine()` 引擎工厂、`SessionLocal`、`init_db()`、`get_db()` 依赖）+ `app/database/repository.py`（`VideoRepository` / `TaskRepository` / `ResultRepository`，路由层禁止直接 SQL）
- [x] 3.3 初始化挂入 `main.py` lifespan（启动即建表，幂等 create_all）；CRUD + 状态流转时间戳自动打点（running→started_at，success/failed→finished_at，failed 记 error_message）
- [x] 3.4 `tests/test_database.py` 7 条用例：建表、Video CRUD、状态机全流转、failed 记录错误、非法状态拒绝、外键约束、级联删除
- [x] 3.5 commit：`feat: add video analysis database`（见下）

### 实现要点

- SQLite `PRAGMA foreign_keys=ON` 通过引擎工厂的 connect 事件统一打开（生产库与测试库复用同一工厂，保证测试测的就是真实行为）。
- `DATABASE_URL` 改用 `.as_posix()`（Windows 反斜杠路径在 sqlite URL 中不可靠）。
- Repository 变更方法自动 `commit`；`update_status` 先校验状态合法性（非法抛 `InvalidStatusError`，不污染数据）。

### 遇到的问题与解决（按"分析→定位→修改→再测试"）

**问题：测试全绿但真实库 `tables: []`（没有建表）。**

1. 分析：真实库由 lifespan 的 `init_db()` 创建，但测试一直没触发过它；
2. 定位：`tests/test_api.py` 写的是 `client = TestClient(app)` —— **没有 `with` 上下文，FastAPI 的 lifespan 根本不执行**（阶段一就是这么写的，`ensure_dirs` 也一直没在测试中跑过，只是恰好目录在磁盘上有）；
3. 修改：改为 fixture 形式 `with TestClient(app) as test_client: yield`；并新增 `test_lifespan_creates_database_tables` 断言真实库含全部业务表；
4. 再测试：`14 passed`；随后用独立进程查真实库 → `['analysis_results', 'analysis_tasks', 'videos']` ✅

### 验证结果

- `pytest` 全量：**14 passed**（2 API+1 lifespan 建表 + 4 视频 + 7 数据库），仅剩已知 starlette 弃用提示。
- 真实库初始化：`data/database/video_agent.db` 三张表齐全（独立进程 `inspect(engine)` 验证，非测试库）。
- 状态机与约束：非法状态被拒且数据未污染、外键 IntegrityError、级联删除，均有断言并通过。

### 个人确认

（待用户实际运行确认后填写）

---

## 阶段四：视频上传 API — 2026-09-30

### 任务范围

对应 `计划书/详细步骤.md` 阶段四（4.1 ~ 4.5）：`POST /videos`、`GET /videos/{video_id}`、Pydantic 出入参、统一异常处理、测试。

### 完成内容

- [x] 4.1 `app/api/routes_videos.py`：`POST /videos` —— 扩展名检查（400）→ 1MB 分块边写边计数大小检查（超限 413，异常/超限均删除残留文件）→ 保存 `data/uploads/{uuid8}_{原名}`（防同名冲突、`Path(...).name` 防路径穿越）→ 调阶段二 `get_video_info` 填 duration/width/height/fps（解析失败删文件后抛 `VideoServiceError`）→ `VideoRepository.create` → **201** 返回 `{"id", "filename"}`
- [x] 4.2 `GET /videos/{video_id}` → Pydantic `VideoDetailResponse`（id, filename, duration, width, height, fps, created_at）；不存在 → 404 `{"detail": "视频不存在：id=..."}`
- [x] 4.3 `app/schemas/video.py`（`VideoCreateResponse` / `VideoDetailResponse`，`response_model` 强制出入参校验）；`main.py` 注册 `VideoServiceError` 全局 handler：`VideoNotFoundError→404`、`InvalidVideoError→400`、其他（含 `FFmpegNotFoundError`）→500，envelope 统一为 `{"detail": ...}`（与 FastAPI 默认 `HTTPException` 响应格式一致）
- [x] 4.4 `tests/test_upload.py` 5 条：正常上传（201 + 返回体 + 文件落盘 + 元数据）、非法扩展名 400、伪装 mp4 的垃圾内容 400 且残留文件被清理、超大文件 413（monkeypatch `MAX_UPLOAD_SIZE_MB=0`）、视频不存在 404
- [x] 4.5 commit：`feat: add video upload API`（见下）

### 实现要点

- **测试隔离**：上传测试用 fixture 把 `config.UPLOADS_DIR` monkeypatch 到 `tmp_path`、用 `create_db_engine` 建临时 SQLite + `app.dependency_overrides[get_db]`，测试不碰真实 `data/uploads` 与真实库（已实测：真实 uploads 只剩 `.gitkeep`、真实库 videos 行数 0）。
- `sample_video` fixture 从 `tests/test_video.py` 上移到 `tests/conftest.py`（`FIXTURES_DIR` 一并上移），上传测试与视频解析测试共用。
- 顺手加固 `test_ffmpeg_missing`：原先依赖"别的测试先跑过导致 fixture 文件已存在"的隐式顺序，改为 `tmp_path` 占位文件，单跑该用例也成立。

### 遇到的问题与解决（按"分析→定位→修改→再测试"）

1. **收集期报错 `RuntimeError: Form data requires "python-multipart" to be installed.`**：FastAPI 的 `UploadFile/File` 依赖 python-multipart → 安装 `python-multipart 0.0.32` 并登记 `requirements.txt`（`python-multipart>=0.0.9`）→ 重跑全绿。
2. **核验脚本两次失败（核验手段问题，非功能问题）**：`python -c` 传多行代码时 PowerShell 把内嵌引号剥掉（`NameError: name 'roundtrip' is not defined`）；改写临时文件后又因脚本在 `%TEMP%` 导入不到 `app`（`ModuleNotFoundError`）→ 临时文件内手动 `sys.path.insert(0, 项目根)` 解决。

### 验证结果

- `pytest` 全量：**19 passed**（3 API + 4 视频 + 7 数据库 + 5 上传）。
- 真实环境 `get_db` 会话往返（独立进程、非测试库）：建→查→删全通过；真实库 `['analysis_results', 'analysis_tasks', 'videos']` 三表齐全，`videos` 行数 0（测试零污染）。
- 隔离性：真实 `data/uploads/` 仅 `.gitkeep`；`tests/fixtures/sample.mp4` 存在（fixture 正常）。

### 个人确认

（待用户实际运行确认后填写）

---

## 阶段五：异步分析任务 — 2026-09-30

### 任务范围

对应 `计划书/详细步骤.md` 阶段五（5.1 ~ 5.6）：创建异步分析任务并立即返回 task_id、任务状态查询、后台异常写入 `task.error_message` 且服务不崩溃、占位分析流程、`tests/test_tasks.py`、commit。

### 完成内容

- [x] 5.1 `app/api/routes_tasks.py`：`POST /videos/{video_id}/analyze` —— 视频不存在 404 → `TaskRepository.create`（pending）→ **202 立即返回** `{"task_id", "video_id", "status": "pending"}` → 响应发出后由 FastAPI `BackgroundTasks` 在线程池中调用 `run_analysis_task(task.id, db.get_bind())` 执行
- [x] 5.2 `GET /tasks/{task_id}` → `TaskDetailResponse`（task_id, video_id, status, created_at, started_at, finished_at, error_message）；不存在 404 `{"detail": "任务不存在：id=..."}`；新增 `app/schemas/task.py`
- [x] 5.3 `app/services/task_service.py::run_analysis_task`：整体 try/except —— 任意异常先 `rollback`、记 ERROR 日志（含 traceback），再 `update_status(failed, error_message=str(exc))`；连失败状态都写不进去时只记日志；`finally` 关闭 Session。**任何异常都不向外抛出，服务不崩溃。**
- [x] 5.4 占位分析 `placeholder_analyze(session, video)`：校验视频文件存在（缺失抛 `AnalysisError`）+ 生成占位 summary 写入 `AnalysisResult`；标注 `TODO(阶段九 9.4)` 替换为真实链路（音频提取 → 转写 → AI 分析）
- [x] 5.5 `tests/test_tasks.py` 6 条：创建立即返回 pending、状态流转（pending→running→success）、文件缺失失败路径 + 服务存活、未预期异常（RuntimeError）只落库不上抛、视频 404、任务 404；并把阶段四的 `client` 隔离 fixture 从 `test_upload.py` 下沉到 `tests/conftest.py` 供两阶段共用
- [x] 5.6 commit：`feat: add async analysis task`（见 git log）

### 实现要点

- **后台任务与请求同库、不同会话**：路由把 `db.get_bind()`（引擎）交给后台，后台用 `sessionmaker(bind=engine)` 新建独立 Session；测试经 `dependency_overrides[get_db]` 用临时库时，后台自动落在同一个临时库上 → 测试零污染（实测真实库 videos/tasks/results 全 0）。
- 请求会话在提交后无未完成事务，SQLite 不会出现写锁互斥。
- 后台执行器放在 `app/services/task_service.py`（Service 层），路由只负责创建任务与排队，符合分层约定。

### 验证结果

- `pytest` 全量：**25 passed**（19 旧 + 6 任务新），仅剩已知 starlette 弃用提示。
- **真实环境探针** `tools/task_probe.py`（对真实 uvicorn 服务，12 项检查全 PASS，exit=0）：
  - `POST /videos/{id}/analyze` **0.214s 返回 202 + pending**（证明是立即返回，非同步等待）；
  - 轮询 `GET /tasks/{id}` 到 `success`，占位 `AnalysisResult.summary` 落库；
  - 删除视频文件后再分析 → 观察到状态流转 `['pending', 'running', 'failed']`，`error_message="视频文件不存在：H:\...data\uploads\b4169fe3_probe.mp4"`；
  - 失败后 `/health` 200、`GET /videos/{id}` 200（服务未崩溃）；不存在的视频/任务均 404；
  - 探针自清理：真实库 `videos=0, tasks=0, results=0`，`data/uploads` 仅 `.gitkeep`；8000 端口干净释放。
- 服务端日志确认 `app.services.task_service` 的"任务启动/任务完成/任务失败"日志与失败 traceback 正常输出。

### 遇到的问题与观察

1. **自查修掉一处笔误**：初版 `routes_tasks.py` 的 `_detail_response` 误留了一个 walrus 占位表达式（`task_video_id := task.video_id`），提交前自查发现，改回 `video_id=task.video_id`（未流入测试）。
2. **TestClient 会等 BackgroundTasks 执行完才返回请求**：因此 HTTP 层测试看到的终态是 success/failed，无法直接观察中间态。解决：创建响应断言 `pending`；`running` 通过 monkeypatch `placeholder_analyze` 挂钩子，在后台线程的 Session 里读取分析开始那一刻的任务状态并断言。
3. 探针中文输出在 GBK 控制台显示乱码（仅控制台显示问题，断言全过、exit=0）。

### 个人确认

（待用户实际运行确认后填写）

---

## 阶段六：音频提取 — 2026-09-30

### 任务范围

对应 `计划书/详细步骤.md` 阶段六（6.1 ~ 6.4）：`extract_audio` 用 FFmpeg 提取 wav、自动命名输出到 `data/outputs/{video_id}/` 避免冲突、异常处理 + logging、测试、commit。

### 完成内容

- [x] 6.1 `app/services/audio_service.py`：`extract_audio(video_path, video_id, *, output_root=None) -> Path` —— FFmpeg 提取音轨为 **16kHz 单声道 PCM s16le wav**（阶段七 mimo 语音转文字的输入规格）
- [x] 6.2 输出 `{output_root}/{video_id}/audio.wav`（缺省 `config.OUTPUTS_DIR` 即 `data/outputs/{video_id}/`）；同名冲突自动编号 `audio_1.wav`… 不覆盖；`video_id` 做路径穿越校验；成功记 INFO、ffmpeg 失败记 WARNING 并抛异常；超时 = 60s + 2×时长；失败/超时清理半成品 wav
- [x] 6.3 `tests/test_audio.py` 6 条 + conftest 新增 `no_audio_video` session fixture（仅 testsrc 视频流）：正常提取（`wave` 校验 1ch/16kHz/16bit/≈2s）、冲突编号、无音频流（`NoAudioStreamError` 且不留残留目录）、文件不存在（`VideoNotFoundError`）、ffmpeg 缺失（`FFmpegNotFoundError`）、ffmpeg 失败（`AudioExtractionError` + 半成品清理）
- [x] 6.4 commit：`feat: add audio extraction`（`a196edb`，3 files, 274 insertions）

### 实现要点

- **复用阶段二校验**：先 `get_video_info`（存在性 → 有效视频 → 音轨探测），复用其 `VideoNotFoundError` / `InvalidVideoError` / `FFmpegNotFoundError` 异常语义（与全局异常处理器一致）；本阶段只新增 `NoAudioStreamError`（无音轨）与 `AudioExtractionError`（提取失败/超时/空输出），基类 `AudioServiceError`。
- 签名比计划书 `extract_audio(video_path)` 多一个 `video_id`：6.2 要求输出到 `data/outputs/{video_id}/`，目录由 video_id 决定，必须显式传入；另加关键字参数 `output_root` 供测试重定向到 `tmp_path`，测试零污染真实 `data/`。
- 定位 ffmpeg 与阶段二定位 ffprobe 同构（`config.FFMPEG_DIR` → PATH），且运行时读 `config.FFMPEG_DIR` 动态属性，便于测试 monkeypatch。

### 验证结果

- `pytest` 全量：**31 passed**（25 旧 + 6 新），仅剩已知 starlette 弃用提示。
- **真实路径实测**（真实 `config.OUTPUTS_DIR`）：提取生成 `data/outputs/999/audio.wav`（64722 bytes）；再次提取生成 `audio_1.wav`（冲突编号生效，目录内 `['audio.wav', 'audio_1.wav']`）；相对布局 `999\audio_1.wav` 正确；清理后 `data/outputs` 仅剩 `.gitkeep`。
- fixture 真实重建：删除 `tests/fixtures/*.mp4` 后全量重跑仍 31 passed（`sample.mp4`、`no_audio.mp4` 均现场生成）。

### 遇到的问题与观察

1. **GBK 解码警告（新测试暴露的真实缺陷）**：新增 `no_audio_video` fixture 首次生成视频时，pytest 报 `PytestUnhandledThreadExceptionWarning`，reader 线程 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xa2 in position 3625` —— conftest 两个 fixture 的 `subprocess.run(text=True)` 未设 `encoding`，Windows 默认用 GBK 解码 ffmpeg stderr（含中文路径）崩溃。此前 `sample.mp4` 已存在、fixture 提前返回，故一直未触发。修复：两处 fixture 统一 `encoding="utf-8", errors="replace"`；删除两个 fixture mp4 重新生成验证，警告消失、31 passed。
2. 教训：中文 Windows 上 `text=True` 的 subprocess 必须显式 `encoding="utf-8", errors="replace"`，否则"读取错误信息"这个动作本身会先崩溃（应用代码 `video_service`/`audio_service` 一直正确，只有测试 fixture 漏设）。

### 个人确认

（待用户实际运行确认后填写）

---

## 阶段七：语音转文字 — 2026-10-01

### 任务范围

对应 `计划书/详细步骤.md` 阶段七（7.1 ~ 7.6）：统一接口 `TranscriptionService`（Whisper / API / Mock 可切换）、第一版 mimo API 音频输入方案输出 `{"text","segments"}`、超时/限流/重试与降级（失败时任务标记 failed 并记录原因）、Mock 实现、测试无 Key 全绿、commit。

### 完成内容

- [x] 7.1 `app/services/transcription_service.py`：抽象基类 `TranscriptionService` + 工厂 `get_transcription_service()`，按 `config.TRANSCRIPTION_PROVIDER` 在 `mimo`（默认）/ `mock` / `whisper` 间切换
- [x] 7.2 `MimoTranscriptionService`：端点 `POST {MIMO_BASE_URL}/chat/completions`（OpenAI 兼容），`model=mimo-v2.5-asr`，音频以 data URL（`data:audio/wav;base64,…`）走 `messages[0].content[0].type=input_audio`，`Authorization: Bearer` 鉴权；输出契约 `{"text": str, "segments": [{"start","end","text"}]}`
- [x] 7.3 健壮性：httpx 超时 60s；429/5xx/网络错误/超时 → 指数退避重试（`max_retries=2`，封顶 30s，带 `Retry-After` 优先）；401/400 等 4xx 确定性失败不重试；耗尽后抛 `TranscriptionError`（含原因）→ 任务链路落 `task.error_message` 并标记 failed
- [x] 7.4 `MockTranscriptionService`：读 wav 时长生成确定性中文示例文本 + 单段，无 Key 可用；`WhisperTranscriptionService` 为预留接口（调用即报"第一版不启用"）
- [x] 7.5 `tests/test_transcription.py` 20 条 + `tests/test_tasks.py` 新增 2 条链路集成，全部走 `httpx.MockTransport` / Mock 服务，无网络无 Key
- [x] 7.6 commit：`feat: add transcription service`

### 实现要点

- **降级构造 segments**：官方 ASR 响应 `choices[0].message.content` 是纯字符串、不带时间戳 → 单段降级，时长优先级 wav 实际时长（stdlib `wave`）→ 响应 `usage.seconds` → `0.0`。
- **配置集中在 `app/config.py`**：`TRANSCRIPTION_PROVIDER`（env 默认 `mimo`）、`MIMO_API_KEY`、`MIMO_BASE_URL`（默认官方 `https://api.xiaomimimo.com/v1`）、`MIMO_ASR_MODEL`（默认 `mimo-v2.5-asr`）；工厂每次调用读配置，测试可 monkeypatch。无 Key 时 `transcribe` 直接抛"未配置 MIMO_API_KEY"（框架先行，符合《初版建议》节奏）。
- **占位链路升级**：`task_service.placeholder_analyze` 现为 校验文件 → `extract_audio`（阶段六）→ `get_transcription_service().transcribe`（阶段七）→ 占位 summary + 真实 `transcript` 落 `AnalysisResult`；AI 分析仍占位，留 `TODO(阶段九 9.4)`。
- **测试隔离**：`tests/conftest.py` 的 `client` fixture 新增重定向 `config.OUTPUTS_DIR → tmp_path/outputs` 与 `TRANSCRIPTION_PROVIDER → "mock"`，测试全链路零污染、无 Key。
- 探针：新增 `tools/transcription_probe.py`（`mock` / `no-key` 两种模式）；`tools/task_probe.py` 清理步骤补删 `data/outputs/{id}/`（链路接入提取后会留下输出目录）。

### 验证结果

- `pytest` 全量：**53 passed**（31 旧 + 20 转写 + 2 链路集成），仅剩已知 starlette 弃用提示。
- **真实环境（mock 提供方）**：`task_probe.py` 12 项全 PASS（阶段五回归，无回归）；`transcription_probe.py mock` 9 项全 PASS —— 真实 ffmpeg 提取 `data/outputs/1/audio.wav`（64722 bytes）、任务 success、`AnalysisResult.transcript` = Mock 文本、占位 summary 落库。
- **真实环境（默认 mimo、未配 Key）**：`transcription_probe.py no-key` 7 项全 PASS —— 状态流转 `['running','failed']`，`error_message = "未配置 MIMO_API_KEY（环境变量或 app/config.py），无法调用 mimo 转写 API。"`，失败后 `/health` 与视频查询仍 200。
- 收尾核对：探针自清理后真实库 `videos=0, tasks=0, results=0`、`data/uploads` 与 `data/outputs` 仅 `.gitkeep`、8000 端口释放。

### 遇到的问题与观察

1. 测试字节串笔误：`b"\xff\xfxfake-mp3-bytes"` 的 `\xfx` 不是合法 hex 转义，pytest 收集阶段 SyntaxError → 改为 `b"ID3 fake mp3 bytes"`（语法错误优先在收集期暴露，属低级但易犯）。
2. 官方 ASR 不返回时间戳（响应只有纯文本 content + `usage.seconds`）：计划契约却要求 segments —— 已在服务端按音频时长降级为单段，并在模块 docstring 写明该差异；若后续需要逐句时间戳，需换 Whisper 类接口或官方后续能力。
3. 真实 API（mimo-v2.5-asr）联网转写尚未实测：当前环境无 `MIMO_API_KEY`，按《初版建议》框架先行；待用户填写 Key 后用真实音频回归一次。

### 个人确认

（待用户实际运行确认后填写）

---

## 阶段八：关键帧提取 — 2026-10-01

### 任务范围

对应 `计划书/详细步骤.md` 阶段八（8.1 ~ 8.4）：`extract_keyframes` 用 OpenCV 固定间隔抽帧（默认每 30 秒）、输出 `data/outputs/{video_id}/frames/frame_0001.jpg…` 并记录 `timestamp`/`filepath`、测试（正常/短视频/文件不存在）、commit。

### 完成内容

- [x] 8.1 `app/services/keyframe_service.py`：`extract_keyframes(video_path, video_id, *, interval_seconds=30.0, output_root=None)`，OpenCV `VideoCapture` 按 `CAP_PROP_POS_MSEC` seek 抽帧，抽帧时间点 `0, interval, 2*interval… < 时长`
- [x] 8.2 输出 `{output_root}/{video_id}/frames/frame_0001.jpg…`，返回记录 `[{"timestamp": float, "filepath": str}]`；重复提取前先清空该目录旧 `frame_*.jpg`（编号确定性，不残留）
- [x] 8.3 `tests/test_keyframes.py` 8 条：正常（1 秒间隔 2 帧 + JPEG 魔数）、短视频（2 秒 < 默认 30 秒 → 第 0 秒 1 帧）、文件不存在 → `VideoNotFoundError`、非视频 → `InvalidVideoError`、间隔非正数、非法 video_id（路径穿越）、重跑清理旧帧、中文输出根回归防线
- [x] 8.4 commit：`feat: add keyframe extraction`

### 实现要点

- **依赖**：新增 `opencv-python-headless>=4.10.0`（安装 5.0.0.93 + numpy 2.5.3），登记 `requirements.txt`；headless 版无 GUI 依赖，服务器可跑。
- **前置校验复用阶段二**：`get_video_info` 抛 `VideoNotFoundError`/`InvalidVideoError`；新增 `KeyframeServiceError`（参数/目录非法）与 `KeyframeExtractionError`（打开/读帧/编码/写盘失败）。
- **时长驱动的时间点**：ffprobe 时长决定目标点列表；短视频（时长 < 间隔）恒有第 0 秒帧；末尾 seek 超范围时读帧失败则以已抽到的帧为准（首帧即失败才报错）。
- **本阶段只交付服务 + 测试**：按计划书 8.x 范围，不接入任务链路（关键帧供阶段九 9.1 图像输入使用）。

### 验证结果

- `pytest` 全量：**61 passed**（53 旧 + 8 关键帧），仅剩已知 starlette 弃用提示。
- **真实环境实测**（真实 `config.OUTPUTS_DIR` = `data/outputs`）：默认间隔 → `frame_0001.jpg`（t=0）；重跑 1 秒间隔 → 2 帧且旧帧被清理重编号；JPEG 魔数 `\xff\xd8` 合法（13411 / 12914 bytes）；实测后目录已清理。

### 遇到的问题与观察

1. **`cv2.imwrite` 在中文路径下失败（真实环境抓到的真 bug）**：项目根为 `H:\视频分析工程`，`cv2.imwrite` 走窄字符 fopen，UTF-8 路径直接返回 False → `KeyframeExtractionError: 写入关键帧失败`；单测 `tmp_path` 是纯 ASCII 所以全绿未暴露。修复：改用 `cv2.imencode(".jpg", frame)` + Python `Path.write_bytes` 落盘（Unicode 安全），并新增中文输出根回归测试 `test_extract_keyframes_unicode_output_path`。
2. 教训同阶段六：**真实路径（真实项目目录）验证不可省**，tmp_path 全绿不等于真实环境可跑。

### 个人确认

（待用户实际运行确认后填写）

---

## 阶段九：AI 内容分析 — 2026-10-01

### 任务范围

对应 `计划书/详细步骤.md` 阶段九（9.1 ~ 9.7）：`analysis_service.py` 的三个生成函数（mimo API 文本 + 关键帧图像输入）、LLM 结构化 JSON 的 Pydantic 校验与非法 JSON 修复、章节格式、把阶段五占位替换为完整链路、MockLLM 测试无 Key 可跑、里程碑提醒配置 API Key、commit。

### 完成内容

- [x] 9.1 `app/services/analysis_service.py`：`generate_summary` / `generate_keywords` / `generate_chapters`，第一版调用 mimo API（OpenAI Chat Completion 兼容，输入为转写文本 + 关键帧 `image_url` data URL，依官方图像理解文档）。
- [x] 9.2 LLM 返回结构化 JSON → Pydantic 校验（`app/schemas/analysis.py`）；非法 JSON 修复链：去代码围栏 → 括号配平截取（截断按未闭合层级补 `]`/`}`）→ 去尾逗号 → 仍失败抛 `AnalysisServiceError` → 任务 failed 落 `error_message`，程序不崩溃。
- [x] 9.3 章节格式 `[{"start":"00:00","title","summary"}]`，`start` 用正则 `^\d{1,3}:\d{2}(:\d{2})?$` 校验。
- [x] 9.4 `task_service.placeholder_analyze` → `analyze_video`：文件校验 → `extract_audio`（六）→ `transcribe`（七）→ `extract_keyframes`（八）→ 三个生成函数（九）→ keywords/chapters 以 JSON 字符串写入 `AnalysisResult`。
- [x] 9.5 测试全部走 MockLLM（固定 JSON），无 API Key 全绿；`client` fixture 新增 `ANALYSIS_PROVIDER → "mock"`。
- [x] 9.6 里程碑：总体框架完成 → 已在交付消息中提醒用户填写分析 API Key/接口配置。
- [x] 9.7 commit：`feat: add AI content analysis`（见 git log）

### 实现要点

- **LLM 抽象**：`AnalysisLLM.complete(messages) → str`；`MimoAnalysisLLM`（重试策略与转写同源：429/5xx/超时/网络错误重试，Retry-After 优先、封顶 30s，401/400 fail-fast；无 Key 报"未配置 MIMO_API_KEY…"）与 `MockAnalysisLLM`（按 system 里 `TASK=summary|keywords|chapters` 标记返回固定 JSON）；工厂 `get_analysis_llm()` 每次读 `config.ANALYSIS_PROVIDER` 切换。
- **配置**：`config.py` 新增 `ANALYSIS_PROVIDER`（env 默认 `mimo`）与 `MIMO_ANALYSIS_MODEL`（默认 `mimo-v2.6-flash`，官方视觉模型）。
- **成本权衡**：`select_keyframes` 均匀取帧上限 8（`_MAX_KEYFRAMES_IN_PROMPT=8`），避免长视频 120 帧爆 token；三次独立 LLM 调用对应计划书三个函数（非合并单次）。
- **消息构造**：system 含 TASK 标记 + "只返回一个 JSON 对象"；user content 关键帧图像在前、任务文本在后（官方图像理解文档格式）；章节任务附"视频总时长（秒）"。

### 验证结果

- `pytest` 全量：**84 passed**（61 旧 + 21 分析 + 2 链路），仅剩已知 starlette 弃用提示。
- **真实环境（双 mock）**：`task_probe.py` 12/12 PASS（阶段五回归无回归，summary 已是 Mock LLM 文本）；`transcription_probe.py mock` 9/9 PASS（真实 ffmpeg 提取 64722 bytes wav → 转写 → 分析 → 落库全链路）。
- **真实环境（转写 mock + 分析默认 mimo 无 Key）**：`transcription_probe.py no-key` 7/7 PASS —— 流转 `['running','failed']`，`error_message = "未配置 MIMO_API_KEY（环境变量或 app/config.py），无法调用 mimo 分析 API。"`，失败后 `/health` 与视频查询仍 200。
- 收尾核对：探针自清理后真实库 `videos=0, tasks=0, results=0`，`data/uploads` 与 `data/outputs` 仅 `.gitkeep`，8000 端口释放。

### 遇到的问题与观察

1. **JSON 修复链首版漏了数组层级**：括号配平只跟踪 `{}`，截断的章节 JSON（`{"chapters": [...` 未闭合）补 `}` 后留下未闭合 `[` → 解析失败（84 → 1 failed 定位）。修复：改用栈同时跟踪 `{}`/`[]`，截断时按剩余层级补 `]`/`}`；对应测试 `test_parse_llm_json_repairs_unclosed_object`。
2. 官方图像理解响应 `content` 为纯字符串、不带结构化字段 —— 与转写一致，摘要/关键词/章节都必须走 `parse_llm_json` 的解析 + 校验 + 修复链。
3. 真实 API（`mimo-v2.6-flash` 图像 + 文本分析）联网实测待填 Key 后进行（用户明确要求初版完成后再配 Key）。

### 个人确认

（待用户实际运行确认后填写）

---

## 阶段十：Video Analysis Agent + Tool — 2026-10-01

### 任务范围

对应 `计划书\详细步骤.md` 阶段十（10.1 ~ 10.8）：`app/agent/` 三个模块 —— 8 个 Tool、系统提示词与 Tool 描述、LLM→选 Tool→执行→回传→判断 的循环（`MAX_TOOL_CALLS = 10` 防无限调用）、`AgentState` 全程落日志、接入阶段五异步任务、`tests/test_agent.py`、commit。

### 完成内容

- [x] 10.1 `app/agent/tools.py`：8 个 Tool（get_video_info / extract_audio / transcribe_audio / extract_keyframes / generate_summary / generate_keywords / generate_chapters / save_result），每个明确输入 JSON Schema / 输出 / 异常（`ToolError`，中文可读）/ 可独立测试；`build_result_fields` 校验四步齐全。
- [x] 10.2 `app/agent/prompts.py`：`AGENT_SYSTEM_PROMPT`（依赖顺序、每步只调一个工具、不编造、遇 `{"error":…}` 先读错误、全部完成后 save_result 并中文总结）+ `TOOL_DESCRIPTIONS` 8 条。
- [x] 10.3 `app/agent/agent.py`：`run_agent` 循环 —— LLM 决策 → Tool 执行 → `{"role":"tool"}` 回传 → 直到最终 content；Tool 异常不外抛，记入 `state.errors` 后以 `{"error": …}` 回传继续。
- [x] 10.4 `MAX_TOOL_CALLS = 10`：执行前检查，超限抛 `AgentError`（state 挂异常），`current_step="error"`。
- [x] 10.5 `AgentState{video_id, task_id, current_step, tool_calls, intermediate_results, errors}`；关键节点全程 `logger`（启动/每次 tool+args/完成 tool_calls=N errors=N/超限/失败）。
- [x] 10.6 接入阶段五：`run_analysis_task` → `analyze_video(task_id=…)`，`config.AGENT_DRIVER`（默认 `agent`，可切 `direct` 走阶段九直连链路）；Agent 的 save_result 已写 `AnalysisResult` → 任务收尾幂等跳过重复插入。
- [x] 10.7 `tests/test_agent.py` 28 条：Tool 单测（依赖顺序、参数校验、save_result、build_result_fields）、MockLLM 全流程 8 步、超限（3 次上限/`MAX_TOOL_CALLS==10`）、工具失败回传继续、`MimoAgentLLM` MockTransport（无 Key / tool_calls 解析 / 最终 content / 429 Retry-After 重试 / 401 不重试 / 500 耗尽 3 次 / 非法参数 JSON / 空内容）、工厂三态。
- [x] 10.8 commit：`feat: add video analysis agent with tool calling`（见 git log）。

### 实现要点

- **LLM 抽象**：`AgentLLM.decide(messages, tools) -> AgentDecision{tool_calls|content}`；`MimoAgentLLM`（mimo 原生 Tool Calling，官方 openai-api 文档，重试策略与转写/分析同源：429/5xx/超时/网络重试，Retry-After 优先封顶 30s，401/400 fail-fast，无 Key 报"未配置 MIMO_API_KEY…mimo Agent API。"）；`MockAgentLLM`（脚本化 Tool 序列，默认即完整 8 步链路，耗尽后返回中文最终答复）。
- **配置**（`config.py`）：`AGENT_PROVIDER`（env 默认 `mimo`，测试切 `mock`）、`MIMO_AGENT_MODEL`（默认 `mimo-v2.6-flash`）、`AGENT_DRIVER`（env 默认 `agent` / `direct`）。
- **save_result 语义**：无 `task_id` 上下文 → `ToolError`；`ResultRepository.create` 自行 commit（既有约定）；`run_analysis_task` 先 `get_by_task` 再插入，幂等。
- **消息协议**：assistant 带 tool_calls 补全历史；每个 tool_call 一条 `role=tool` 结果（`json.dumps(ensure_ascii=False)`），OpenAI 兼容可回放给真实 API。

### 验证结果

- `pytest` 全量 **112 passed**（84 旧 + 28 Agent），仅剩已知 starlette 弃用提示。
- **真实环境（三 provider 全 mock，AGENT_DRIVER=agent）**：`task_probe.py` 12/12 PASS；`transcription_probe.py mock` 9/9 PASS —— 服务日志确认 Agent 真实跑完 8 步（`Agent 启动 … max_tool_calls=10` → 每步 `tool=xxx args={}` → `Agent 完成 tool_calls=8 errors=0` → success）。
- **真实环境（默认 mimo 无 Key）**：`transcription_probe.py no-key` 7/7 PASS —— `['running','failed']`，`error_message = "未配置 MIMO_API_KEY（环境变量或 app/config.py），无法调用 mimo Agent API。"`，失败后服务正常。
- 收尾核对：真实库 `videos=0, tasks=0, results=0`，`data/uploads` 与 `data/outputs` 仅 `.gitkeep`，8000 端口释放；清理了一个 0 字节无表残留 `data/database/app.db`（历史误建）。

### 遇到的问题与观察

1. **`task_service.py` 漏 `from app import config`**：新增 `AGENT_DRIVER` 读取后首次跑测试 6 failed（`NameError: name 'config' is not defined`）—— 改 config 相关代码要同步检查 import。
2. **两处断言按真实实现修正**：`get_video_info` 的 `filename` 取自 ffprobe（真实文件名 `sample.mp4`，非写库名）；Tool 描述不含工具名本身 → 断言改验语义（"最后一步"）。
3. spy/boom 签名补 `task_id=None` 并转发（`analyze_video` 新增 kw-only 参数），否则调用点 TypeError。
4. 真实 API 的 Tool Calling 联网实测待填 Key 后进行（用户要求初版完成后再配 Key）。

### 个人确认

（待用户实际运行确认后填写）
