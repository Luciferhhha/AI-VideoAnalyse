# 视频智能分析平台（video-agent）

AI Coding Agent 驱动的视频智能分析与内容工程平台：上传视频 → 解析元数据 → 提取音频/关键帧 → 语音转文字 → AI 生成摘要/关键词/章节，整条链路由 **Video Analysis Agent 通过 Tool Calling 驱动**，以异步任务（202 + 轮询）对外服务。

## 1. 简介

- 上传视频（≤500MB，6 种格式）自动用 ffprobe 解析元数据；
- 一键发起分析任务，后台完成：音频提取（FFmpeg）→ 语音转文字（mimo API）→ 关键帧抽取（OpenCV）→ AI 摘要/关键词/章节（mimo 多模态）→ 结果落库；
- 核心链路是 LLM 驱动的 8-Tool Agent 循环，也保留直连链路作对照与降级；
- 本地**控制面板**（`GET /`，单文件 HTML）覆盖：视频上传、分片/关键帧统计、分析结果、API 控制、日志流程；
- 开发全程由 AI Coding Agent 按计划书逐阶段推进（阶段一~十四），每阶段验证→commit→登记日志。

## 2. 特点

- **Agent Tool Calling**：8 个 Tool、`MAX_TOOL_CALLS=10` 上限、错误回填可自纠，全程落日志；
- **控制面板**：零构建的单文件静态页（`app/static/index.html`），纯原生 JS/CSS，无外部 CDN；`/docs` 仍为 Swagger 文档；
- **异步任务**：`POST /videos/{id}/analyze` 立即 202，后台执行，状态机 `pending→running→success|failed`，失败带 `error_message` 且服务不崩溃；
- **无 API Key 可跑**：三处 provider（转写/分析/Agent）均可切 `mock`，全量测试不依赖 Key；
- **可切换接口**：本地 Whisper 接口预留（第一版不启用），mimo 为唯一外部依赖；
- **严格分层**：路由禁止直接写 SQL（一律经 Repository）；
- **真实可验证**：探针脚本在真实服务上逐项 PASS/FAIL，测试报告见 `docs/test-report.md`。

## 3. 架构

```
用户 → FastAPI(API 层) → Task Manager(状态机) → Agent(LLM 决策循环)
     → Tool Layer(8 Tools) → Service 层(FFmpeg/OpenCV/转写/分析)
     → Repository → SQLite；外部：FFmpeg / OpenCV / mimo API
```

完整分层图与四层依赖规则见 [docs/architecture.md](docs/architecture.md)。

## 4. 技术栈

| 领域 | 选型 |
|---|---|
| Web 框架 | FastAPI + uvicorn（Pydantic v2 校验） |
| ORM / 数据库 | SQLAlchemy 2.x / SQLite（`data/database/video_agent.db`） |
| 外部工具 | FFmpeg（ffprobe 元数据、音频提取）、OpenCV（关键帧） |
| AI | mimo API（OpenAI 兼容）：ASR `mimo-v2.5-asr`、视觉/Agent `mimo-v2.6-flash` |
| HTTP 客户端 | httpx（含 `MockTransport` 做无网络单测） |
| 测试 | pytest（fixture 生成式视频、TestClient、真实探针） |
| 运行环境 | Windows 中文路径、Python 3.13 |

## 5. 目录结构

```
video-agent/
├── app/
│   ├── api/            # 路由（routes_videos / routes_tasks / routes_panel）
│   ├── agent/          # prompts.py / tools.py / agent.py（Tool Calling 核心）
│   ├── database/       # models + repository（唯一写 SQL 处）
│   ├── schemas/        # Pydantic 契约
│   ├── services/       # video/audio/transcription/keyframe/analysis/task/log/panel
│   ├── static/         # 控制面板单文件页 index.html（GET / 返回）
│   ├── config.py       # 全部配置与环境变量
│   └── main.py         # FastAPI 入口、logging、全局异常
├── tests/              # 134 条测试（conftest.py 共享 fixture）
├── tools/              # 真实环境探针（task_probe / transcription_probe）
├── docs/               # architecture / api / agent / agent-development / test-report / development-log
├── data/               # uploads / outputs / database（运行时生成，不入库）
├── requirements.txt
└── run.py              # 启动入口
```

## 6. 安装

```powershell
git clone <repo> && cd video-agent
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt   # fastapi uvicorn pydantic sqlalchemy httpx python-multipart pytest opencv-python-headless
```

## 7. FFmpeg

- 需要 `ffmpeg` / `ffprobe` 可执行文件：**加入 PATH**，或在 `app/config.py` 设置 `FFMPEG_DIR`（留空即走 PATH）；
- 缺失时：健康检查与静态接口仍可用，涉及媒体解析的请求返回 500（`FFmpegNotFoundError`）；
- 验证：`ffprobe -version`。

## 8. 环境变量（全部可选，见 `app/config.py`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MIMO_API_KEY` | 空 | mimo API Key（初版完成前不填，验证走 mock） |
| `MIMO_BASE_URL` | `https://api.xiaomimimo.com/v1` | OpenAI 兼容端点 |
| `TRANSCRIPTION_PROVIDER` | `mimo` | `mimo` / `mock` / `whisper`（预留） |
| `ANALYSIS_PROVIDER` | `mimo` | `mimo` / `mock` |
| `AGENT_PROVIDER` | `mimo` | `mimo` / `mock` |
| `AGENT_DRIVER` | `agent` | `agent`（Agent 循环）/ `direct`（阶段九直连） |
| `MIMO_ASR_MODEL` / `MIMO_ANALYSIS_MODEL` / `MIMO_AGENT_MODEL` | `mimo-v2.5-asr` / `mimo-v2.6-flash` / `mimo-v2.6-flash` | 模型 |
| `FFMPEG_DIR` | 空（PATH） | FFmpeg 目录 |

## 9. 启动

```powershell
.venv\Scripts\python run.py          # http://127.0.0.1:8000
curl http://127.0.0.1:8000/health    # {"status": "ok"}
```

启动后浏览器打开 **http://127.0.0.1:8000/** 即为控制面板（`/docs` 仍是 Swagger 文档）。

无 Key 全 mock 模式（本地体验整条链路）：

```powershell
$env:AGENT_PROVIDER='mock'; $env:TRANSCRIPTION_PROVIDER='mock'; $env:ANALYSIS_PROVIDER='mock'
.venv\Scripts\python run.py
```

## 10. API 示例

```powershell
curl -X POST http://127.0.0.1:8000/videos -F "file=@demo.mp4"        # 201 {"id":1,...}
curl http://127.0.0.1:8000/videos                                    # 200 列表（关键帧/章节/任务聚合）
curl http://127.0.0.1:8000/videos/1                                  # 200 元数据
curl -X POST http://127.0.0.1:8000/videos/1/analyze                  # 202 {"task_id":1,"status":"pending"}
curl http://127.0.0.1:8000/tasks/1                                   # 200 轮询状态/错误
curl "http://127.0.0.1:8000/logs?limit=20"                           # 200 最近日志（面板「日志流程」）
curl http://127.0.0.1:8000/settings                                  # 200 运行配置快照（不含 API Key）
```

完整接口、状态码与响应字段见 [docs/api.md](docs/api.md)。

## 11. Agent 流程

`pending → running` 时后台启动 Agent：`get_video_info → extract_audio → transcribe_audio → extract_keyframes → generate_summary → generate_keywords → generate_chapters → save_result`，每步一条日志，完成 `tool_calls=8 errors=0`；超 10 次调用即终止。详见 [docs/agent.md](docs/agent.md)。

## 12. Tool Calling 说明

8 个 Tool 以 OpenAI function calling schema 下发（`TOOL_SCHEMAS`），LLM 每步返回 `tool_calls` 取第 1 个执行；输出回填 `AgentState.intermediate_results`，错误回填给 LLM 自纠；`save_result` 校验转写+三生成齐全后经 `ResultRepository` 落库。详见 [docs/agent.md](docs/agent.md)。

## 13. 测试方法

```powershell
.venv\Scripts\python -m pytest -q        # 134 passed, 1 warning（无 API Key）
```

- 全部测试**不需要** `MIMO_API_KEY`（mock provider + `httpx.MockTransport`）；
- 真实环境探针：`.venv\Scripts\python tools\task_probe.py` / `tools\transcription_probe.py mock|no-key`（自清理）；
- 测试报告与十类覆盖对照：[docs/test-report.md](docs/test-report.md)。

## 14. 示例运行结果（实测）

- 全量测试：`134 passed, 1 warning in 7.58s`（warning 为已知 starlette testclient 弃用提示，含 15 条控制面板测试）；
- 控制面板实测：`GET /` 200（28KB 单文件 HTML，五大区块齐全）、`/videos` 返回既有 9 个视频（如 `keyframe_count=10`、`chapter_count=4`、`latest_task_status=success`）、`/tasks` 4 条、`/settings` 快照 `interval=5.0 / ffmpeg=true / key_configured=false`、`/logs` 捕获到 `app.main startup…`、`/docs` 仍为 Swagger；
- 探针：`task_probe` 12/12 PASS、`transcription_probe mock` 9/9 PASS、`no-key` 7/7 PASS；
- 真实日志链：`Agent 启动 video_id=1 task_id=1 max_tool_calls=10` → 8×`Agent 工具调用 tool=… args={}` → `音频提取完成 … 64722 bytes` → `转写完成 provider=mock … 时长=2.0s` → `Agent 完成 tool_calls=8 errors=0` → `任务完成 task_id=1 status=success`；
- 无 Key 失败路径：任务 `failed`，`error_message="未配置 MIMO_API_KEY（环境变量或 app/config.py），无法调用 mimo Agent API。"`，`/health` 与视频查询仍 200。

## 15. AI Coding 过程

由 AI Coding Agent 按《计划书》阶段一~十四逐阶段推进：每阶段「读条目 → 勘察 → 实现 → 单测 → 真实环境 → 四步修复法 → commit → 登记日志」。真实 Bug 修复案例（中文路径 `cv2.imwrite` 静默失败、JSON 修复链漏数组层级、签名变更漏接缝）见 [docs/agent-development.md](docs/agent-development.md)；逐阶段记录见 [docs/development-log.md](docs/development-log.md)。

## 16. 已知问题

- 任务详情接口只返回状态；分析产物用 `GET /tasks/{id}/result` 查询（成功任务返回摘要/关键词/章节/转写，未完成或失败为 409）；
- 唯一 warning：starlette `TestClient`+httpx 弃用提示（第三方，升级跟踪）；
- `tests/test_api.py` 自带一个非隔离 `client` fixture（lifespan 用真实库），与 conftest 隔离 fixture 同名不同行为；
- 中文 Windows 控制台日志为 GBK 显示乱码（仅显示问题，写入与逻辑均为 UTF-8）；
- 真实 mimo API 链路**尚未用真实 Key 回归**（按约定初版完成后填 Key 验证）；
- `whisper` 提供方仅预留接口，调用即报「第一版不启用」；
- 控制面板日志为**进程内环形缓冲**（1000 条，重启即清空），不是文件日志；`logs/launcher.log` 仅记录启动信息；
- 面板列表无分页/搜索（`GET /videos`、`GET /tasks` 全量返回，本地小数据量够用）。

## 17. 后续计划

- 填写 `MIMO_API_KEY` 后对转写/分析/Agent 三链路做真实 API 回归；
- 分析结果列表接口（按视频列出历史任务结果）；
- 控制面板增强：多文件上传队列、结果导出、日志过滤与导出（第一版为单文件简洁面板）；
- 计划书阶段十五（另行启动）：整理 12 项交付物、简历描述、GitHub 开源；
- 本地 Whisper 提供方落地（接口已预留）、多任务并发与限流优化。
