# 系统架构说明

> 对应《计划书》阶段十三 13.1。描述视频智能分析平台（video-agent）的分层架构与各层职责。

## 1. 分层架构图

```
用户 / curl / 前端
        │  HTTP
        ▼
┌─────────────────────────────────────────────┐
│  API 层（FastAPI）                          │
│  app/api/routes_videos.py  /videos …        │
│  app/api/routes_tasks.py   /tasks …         │
│  app/main.py  /health、全局异常 handler      │
└───────────────┬─────────────────────────────┘
                │ 仅经 Dependency(get_db) 拿 Session，
                │ 202 立即返回 + BackgroundTasks 异步化
                ▼
┌─────────────────────────────────────────────┐
│  Task Manager（任务状态机）                  │
│  app/services/task_service.py               │
│  pending → running → success / failed       │
│  （独立 Session 跑后台任务，异常只落库不外抛）│
└───────────────┬─────────────────────────────┘
                │ AGENT_DRIVER=agent（默认）
                ▼
┌─────────────────────────────────────────────┐
│  Agent 层（Video Analysis Agent + Tool）    │
│  app/agent/agent.py   LLM 决策循环           │
│  app/agent/prompts.py 系统提示词/Tool 描述   │
│  app/agent/tools.py   8 个 Tool + 执行器     │
└───────────────┬─────────────────────────────┘
                │ execute_tool(name, args, context, state)
                ▼
┌─────────────────────────────────────────────┐
│  Service 层（业务能力，封装外部工具）         │
│  video_service    ffprobe 解析元数据          │
│  audio_service    FFmpeg 提取 wav            │
│  transcription_service  语音转文字（mimo/mock│
│  keyframe_service OpenCV 抽帧                │
│  analysis_service 摘要/关键词/章节（mimo/mock│
└───────┬─────────────────────┬───────────────┘
        │                     │
        ▼                     ▼
 外部工具 / API            Repository 层
 FFmpeg / OpenCV           app/database/repository.py
 mimo API（OpenAI 兼容）    Video/Task/ResultRepository
 （唯一允许写 SQL 的地方）
        │                     │
        ▼                     ▼
  文件系统 data/outputs/   SQLite data/database/video_agent.db
        data/uploads/
```

## 2. 四层关系：Agent / Service / Repository / API

| 层 | 位置 | 职责 | 依赖方向 |
|---|---|---|---|
| API 层 | `app/api/` | HTTP 协议适配：参数校验、状态码、schema 序列化；创建任务后用 BackgroundTasks 异步执行 | 只依赖 Repository（查视频、建任务）与 Task Manager |
| Agent 层 | `app/agent/` | 决策与编排：LLM 选择下一个 Tool → 执行 → 把结果回填 `AgentState` → 循环直至 `save_result` 或超限 | 只依赖 Service 层与 Repository（save_result），**不直接碰 HTTP/SQL** |
| Service 层 | `app/services/` | 单一业务能力：FFmpeg/OpenCV/转写/分析的封装、异常翻译成领域异常 | 依赖 Repository（读视频元数据）、外部进程/HTTP 客户端 |
| Repository 层 | `app/database/` | **唯一允许写 SQL 的地方**（计划书全局约束）：`VideoRepository/TaskRepository/ResultRepository`，内部自行 commit | 依赖 SQLAlchemy Session |

依赖规则（自上而下单向，禁止反向）：

- 路由**禁止直接写 SQL**，一律经 Repository（阶段四约束，`routes_*` 只调 `VideoRepository/TaskRepository`）。
- Agent 的 Tool 只调 Service 函数与 `ResultRepository.create`，不 import FastAPI。
- Service 不 import `app.api`，可被 Agent 与阶段九直连链路（`AGENT_DRIVER=direct`）复用。

## 3. 数据流（一次完整分析）

```
POST /videos（上传）           → videos 行（ffprobe 元数据）
POST /videos/{id}/analyze      → 202 + analysis_tasks 行(pending)
  └─ 后台 run_analysis_task：
     Agent 循环 8 步：
       get_video_info → extract_audio → transcribe_audio
       → extract_keyframes → generate_summary → generate_keywords
       → generate_chapters → save_result（写 analysis_results + success）
失败任一步 → 任务 failed + error_message（服务不崩溃）
GET /tasks/{id}                → 轮询状态与结果
```

- 运行时产物：`data/uploads/`（原视频）、`data/outputs/{video_id}/audio.wav`、`data/outputs/{video_id}/frames/frame_000N.jpg`。
- 数据库：`data/database/video_agent.db`（SQLite，`init_db()` 启动建表）。

## 4. 配置与可切换点

| 配置项 | 取值 | 作用 |
|---|---|---|
| `TRANSCRIPTION_PROVIDER` | `mimo` / `mock` / `whisper`（预留） | 转写实现 |
| `ANALYSIS_PROVIDER` | `mimo` / `mock` | 摘要/关键词/章节 LLM |
| `AGENT_PROVIDER` | `mimo` / `mock` | Agent 决策 LLM |
| `AGENT_DRIVER` | `agent`（默认）/ `direct` | 任务链路：Agent 循环 vs 阶段九直连 |
| `MIMO_API_KEY` / `MIMO_BASE_URL` / `MIMO_*_MODEL` | — | mimo API 凭据与模型 |
| `FFMPEG_DIR` | 留空走 PATH | FFmpeg 位置 |

全部可由环境变量覆盖（见 `app/config.py`）；三处 provider 独立切换，测试与无 Key 环境全走 `mock`（见 `docs/test-report.md`）。

## 5. 关键设计决策

1. **异步任务**：API 202 立即返回，后台任务持独立 Session（`sessionmaker` 新建），互不阻塞；任何异常只落 `analysis_tasks.error_message`，不冒泡到服务（阶段五 5.3）。
2. **Agent 与直连双链路**：`AGENT_DRIVER` 一键切换。Agent 是产品核心，直连链路保留作对照与降级（阶段九→十的增量演进）。
3. **LLM 三次独立调用**（摘要/关键词/章节）对应计划书三函数，各自 Pydantic 契约校验 + JSON 修复链（去 code fence → 括号配平截取 → 去尾逗号 → 模型校验），失败抛错不崩溃。
4. **中文路径安全**：`cv2.imwrite` 在中文 Windows 路径下失败，统一改 `cv2.imencode` + `Path.write_bytes`（见 `docs/agent-development.md` 案例）。
5. **日志**：`logging.basicConfig(INFO)` + 各模块 `logger`，关键事件（任务启动/完成/失败、音频提取、转写完成、Agent 每次 Tool 调用）全量覆盖；`app/` 包内零 print（阶段十二）。
