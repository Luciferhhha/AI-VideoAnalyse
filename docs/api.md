# HTTP API 文档

> 对应《计划书》阶段十三 13.2。Base URL 默认 `http://127.0.0.1:8000`（由 `run.py` 启动，uvicorn）。
> 所有响应为 JSON；错误响应统一 envelope：`{"detail": "<原因>"}`。

## 1. GET /health

健康检查。

```bash
curl http://127.0.0.1:8000/health
```

```json
{"status": "ok"}
```

## 2. POST /videos — 上传视频

`multipart/form-data`，字段名 **`file`**。

| 项 | 说明 |
|---|---|
| 允许扩展名 | `.mp4 .avi .mov .mkv .flv .webm`（`ALLOWED_VIDEO_EXTENSIONS`） |
| 大小上限 | 500 MB（`MAX_UPLOAD_SIZE_MB`） |
| 成功 | `201 Created` |

成功响应（`VideoCreateResponse`）：

```json
{"id": 1, "filename": "demo.mp4"}
```

上传即用 ffprobe 解析元数据（时长/分辨率/帧率）存入数据库。

错误：

| 状态码 | 场景 |
|---|---|
| 400 | 扩展名不允许 / 空文件 |
| 413 | 超过 500 MB |
| 422 | 缺少 `file` 字段（FastAPI 参数校验） |
| 500 | 服务端 FFmpeg 缺失（`FFmpegNotFoundError`） |

示例：

```bash
curl -X POST http://127.0.0.1:8000/videos -F "file=@demo.mp4"
```

## 3. GET /videos/{video_id} — 查询视频

成功 `200`（`VideoDetailResponse`）：

```json
{"id": 1, "filename": "demo.mp4", "duration": 2.0, "width": 320, "height": 240, "fps": 10.0, "created_at": "2026-10-01T12:00:00"}
```

`duration/width/height/fps` 可为 `null`（ffprobe 缺项）。视频不存在 → `404`；非整数 id → `422`；非视频/解析失败 → `400`。

```bash
curl http://127.0.0.1:8000/videos/1
```

## 4. POST /videos/{video_id}/analyze — 发起分析任务（异步）

`202 Accepted` 立即返回，任务在后台执行（阶段五 5.3：接口不等待分析完成）。

成功响应（`TaskCreateResponse`，快照为创建时刻）：

```json
{"task_id": 1, "video_id": 1, "status": "pending"}
```

- 视频不存在 → `404 {"detail": "视频不存在：id=999"}`
- 非整数 video_id → `422`

```bash
curl -X POST http://127.0.0.1:8000/videos/1/analyze
```

## 5. GET /tasks/{task_id} — 查询任务状态

成功 `200`（`TaskDetailResponse`）：

```json
{
  "task_id": 1,
  "video_id": 1,
  "status": "success",
  "created_at": "2026-10-01T12:00:00",
  "started_at": "2026-10-01T12:00:01",
  "finished_at": "2026-10-01T12:00:03",
  "error_message": null
}
```

- `status` 取值：`pending → running → success | failed`
- 失败时 `error_message` 为具体原因（如 `视频文件不存在：…`、`未配置 MIMO_API_KEY…`），服务保持存活
- 任务不存在 → `404 {"detail": "任务不存在：id=999"}`

```bash
curl http://127.0.0.1:8000/tasks/1
```

## 6. GET /tasks/{task_id}/result — 查询分析结果

成功 `200`（`TaskResultResponse`），`keywords`/`chapters` 已从库中 JSON 字符串还原为结构：

```json
{
  "task_id": 1,
  "video_id": 1,
  "status": "success",
  "summary": "这段内容先是朗读了一段充满古风意境的歌词…",
  "keywords": ["人间情", "姑娘", "琵琶"],
  "chapters": [{"start": "00:00", "title": "情感叙事开篇", "summary": "…"}],
  "transcript": "人间情悠扬，姑娘把谁记心上…",
  "finished_at": "2026-10-01T14:36:15"
}
```

| 状态码 | 场景 |
|---|---|
| 200 | 任务成功且结果已落库 |
| 404 | 任务不存在 / 成功但结果行缺失（数据异常） |
| 409 | 任务未完成或失败，`detail` 含 `status=…` 与失败原因（如 `错误：视频文件不存在…`） |

```bash
curl http://127.0.0.1:8000/tasks/1/result
```

## 7. 控制面板（阶段十六）

单文件静态页 `app/static/index.html`，由 `GET /` 直接返回（`text/html`），无需构建与前端依赖；
`/docs` 仍为 FastAPI 自带 Swagger，未做任何改造。面板五大区块：视频上传 / 视频分片与关键帧 /
分析结果 / API 控制 / 日志流程，前端每 2.5 秒轮询刷新。

### 7.1 GET / — 面板页面

```bash
curl http://127.0.0.1:8000/
```

`200 text/html`；页面文件缺失时 `404 {"detail": "控制面板页面缺失：…"}`。该路由 `include_in_schema=False`，不出现在 `/docs`。

### 7.2 GET /videos — 视频列表（面板表格数据源）

`200`（`list[VideoListResponse]`，新→旧）：

```json
[{
  "id": 9, "filename": "demo.mp4", "duration": 12.0,
  "width": 320, "height": 240, "fps": 10.0, "created_at": "2026-10-01T12:00:00",
  "keyframe_count": 10, "chapter_count": 4,
  "task_count": 1, "latest_task_id": 4, "latest_task_status": "success"
}]
```

- `keyframe_count`：`data/outputs/{id}/frames/frame_*.jpg` 的**磁盘实数**（未分析为 0，目录异常按 0 兜底）
- `chapter_count`：最近一次**成功**任务结果里的 `chapters` 数组长度（内容分片数；未分析为 0）
- `latest_task_*`：该视频最新任务的状态/编号（`null` 表示尚无任务）
- 空库返回 `[]`（**无分页参数**）

### 7.3 GET /tasks — 任务列表（面板轮询数据源）

`200`（`list[TaskListResponse]`，新→旧）：

```json
[{
  "task_id": 4, "video_id": 9, "video_filename": "demo.mp4",
  "status": "success", "created_at": "…", "started_at": "…",
  "finished_at": "…", "error_message": null
}]
```

`video_filename` 由 `video_id` 关联得到（视频缺失时回退 `#<id>`）。空库返回 `[]`。

### 7.4 GET /logs — 最近日志（面板「日志流程」）

进程内环形缓冲（`app/services/log_service.py`，最多 1000 条，**进程重启即清空**），只读不落盘。

| 参数 | 说明 |
|---|---|
| `limit` | 1–1000，默认 200；返回**旧→新**的最后 N 条 |
| `level` | 可选，`DEBUG/INFO/WARNING/ERROR/CRITICAL`，只保留该级别及以上 |

```bash
curl "http://127.0.0.1:8000/logs?limit=20&level=INFO"
```

```json
{
  "items": [
    {"ts": "2026-10-02 12:48:23", "level": "INFO",
     "logger": "app.services.task_service", "message": "任务启动 task_id=4 video_id=9"}
  ],
  "total": 1
}
```

- `total` 为缓冲内当前条数（不受 `level` 影响），`items` 最长为 `min(limit, 过滤后条数)`
- `limit<1` 或 `limit>1000`、非法 `level` → `422`
- 只采集 INFO 及以上（任务启动/完成、Agent 工具调用、异常堆栈）

### 7.5 GET /settings — 运行配置快照（面板「API 控制」）

`200`（`SettingsResponse`），**只读**，读取请求时刻的 `config`：

```json
{
  "app": {"title": "视频智能分析平台", "version": "0.1.0"},
  "providers": {"transcription": "mimo", "analysis": "mimo", "agent": "mimo", "driver": "agent"},
  "models": {"asr": "…", "analysis": "…", "agent": "…"},
  "mimo": {"base_url": "…", "api_key_configured": false,
           "api_key_masked": null, "api_key_source": "none"},
  "limits": {"max_upload_size_mb": 500, "allowed_extensions": [".avi", ".flv", ".mkv", ".mov", ".mp4", ".webm"]},
  "keyframe_interval_seconds_default": 5.0,
  "ffmpeg_available": true, "ffprobe_available": true
}
```

> 安全约定：`mimo` 块只回传布尔/掩码/来源，**绝不回传 API Key 明文**（有测试守卫）。
> `api_key_source` 取值：`file`（面板密钥文件，优先）/ `env`（环境变量 `MIMO_API_KEY`）/ `config`（运行时写入）/ `none`（未配置）。

### 7.6 API Key 管理（增 / 换 / 删）

面板「API 控制 → API Key 管理」块对应的三个端点，全部**只回传状态与掩码，永不回传明文**。

存储与加密：

| 项 | 说明 |
|---|---|
| 存储位置 | `data/secrets/mimo_api_key.bin`（已加入 `.gitignore`，不入库） |
| 加密方式 | Windows **DPAPI**（`CryptProtectData`，零新增依赖），密文绑定当前 Windows 用户 |
| 生效方式 | `PUT` 成功即写入 `config.MIMO_API_KEY`，**立即生效无需重启**；服务启动时 `load_into_config()` 自动加载 |
| 优先级 | 密钥文件（`file`）> 环境变量 `MIMO_API_KEY`（`env`）> 空（`none`） |

#### GET /api-keys/mimo — 查询 Key 状态

```json
{"configured": true, "masked": "sk-abcd****wxyz", "source": "file",
 "storage": "dpapi", "storage_path": "data\\secrets\\mimo_api_key.bin",
 "updated_at": "2026-10-02 13:27:03", "error": null}
```

`masked` = 首 6 位 + `****` + 末 4 位（过短只回 `****`）；`error` 为密文解密失败时的原因（如跨 Windows 账号拷贝）。

#### PUT /api-keys/mimo — 新增 / 更换 Key

请求体：`{"api_key": "sk-…"}`（1–512 字符）。

| 状态码 | 场景 |
|---|---|
| 200 | 已保存（DPAPI 密文落盘 + 立即写入运行时配置），返回状态对象 |
| 400 | `API Key 不能为空`（空白/仅引号） / `看起来是掩码值（含 ****），请粘贴完整 API Key` |
| 422 | 缺字段、空串或超过 512 字符 |

```bash
curl -X PUT http://127.0.0.1:8000/api-keys/mimo -H "Content-Type: application/json" -d "{\"api_key\":\"sk-…\"}"
```

#### DELETE /api-keys/mimo — 清空 / 删除 Key

删除密钥文件（幂等，文件本就不存在也算成功），并把运行时值回落到环境变量 `MIMO_API_KEY`：

- 环境变量也为空 → `{"configured": false, "source": "none"}`（面板提示「已清空，当前未配置」）
- 环境变量有值 → `{"configured": true, "source": "env", "masked": "…"}`（回退生效）

```bash
curl -X DELETE http://127.0.0.1:8000/api-keys/mimo
```

## 8. 端点总览与状态码速查

| 方法 | 路径 | 成功 | 说明 |
|---|---|---|---|
| GET | `/` | 200 | 控制面板页面（HTML，`/docs` 不受影响） |
| GET | `/health` | 200 | 健康检查 |
| POST | `/videos` | 201 | 上传（file 字段，≤500MB，6 种扩展名） |
| GET | `/videos` | 200 | 视频列表（关键帧/章节/最近任务聚合，新→旧） |
| GET | `/videos/{id}` | 200 | 视频详情 |
| POST | `/videos/{id}/analyze` | 202 | 创建分析任务（异步） |
| GET | `/tasks` | 200 | 任务列表（新→旧，带视频文件名） |
| GET | `/tasks/{id}` | 200 | 任务详情与状态 |
| GET | `/tasks/{id}/result` | 200 | 分析结果（成功任务；未完成/失败 409） |
| GET | `/logs` | 200 | 最近日志（环形缓冲，limit/level 参数） |
| GET | `/settings` | 200 | 运行配置快照（掩码 + 来源，不含明文） |
| GET | `/api-keys/mimo` | 200 | API Key 状态（是否配置、掩码、来源、存储路径） |
| PUT | `/api-keys/mimo` | 200 | 新增 / 更换 API Key（DPAPI 加密落盘，立即生效；空值/掩码值 400） |
| DELETE | `/api-keys/mimo` | 200 | 清空 / 删除 API Key（回落环境变量，幂等） |

常见错误码：`400` 非法内容 / `404` 资源不存在 / `409` 状态冲突 / `413` 文件过大 / `422` 参数校验失败 / `500` 服务端依赖缺失。
`VideoServiceError` 由全局 handler 统一翻译（`VideoNotFoundError→404`、`InvalidVideoError→400`、其他→500，见 `app/main.py`）。

## 9. 分析结果字段（存储层）

结果经 `ResultRepository.get_by_task(task_id)` 落库于 `analysis_results`，由 `GET /tasks/{id}/result` 返回：

| 列 | 内容 |
|---|---|
| `summary` | AI 摘要文本 |
| `keywords` | JSON 字符串数组，如 `["测试","视频分析"]`（接口层解析为数组） |
| `chapters` | JSON 数组 `[{"start":"00:00","title":"…","summary":"…"}]`（阶段九 9.3 契约） |
| `transcript` | 语音转写全文 |

> 说明：结果由 Agent 的 `save_result` Tool 经 `ResultRepository` 写入；路由层不直接写 SQL。
