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
