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

## 7. 端点总览与状态码速查

| 方法 | 路径 | 成功 | 说明 |
|---|---|---|---|
| GET | `/health` | 200 | 健康检查 |
| POST | `/videos` | 201 | 上传（file 字段，≤500MB，6 种扩展名） |
| GET | `/videos/{id}` | 200 | 视频详情 |
| POST | `/videos/{id}/analyze` | 202 | 创建分析任务（异步） |
| GET | `/tasks/{id}` | 200 | 任务详情与状态 |
| GET | `/tasks/{id}/result` | 200 | 分析结果（成功任务；未完成/失败 409） |

常见错误码：`400` 非法内容 / `404` 资源不存在 / `409` 状态冲突 / `413` 文件过大 / `422` 参数校验失败 / `500` 服务端依赖缺失。
`VideoServiceError` 由全局 handler 统一翻译（`VideoNotFoundError→404`、`InvalidVideoError→400`、其他→500，见 `app/main.py`）。

## 8. 分析结果字段（存储层）

结果经 `ResultRepository.get_by_task(task_id)` 落库于 `analysis_results`，由 `GET /tasks/{id}/result` 返回：

| 列 | 内容 |
|---|---|
| `summary` | AI 摘要文本 |
| `keywords` | JSON 字符串数组，如 `["测试","视频分析"]`（接口层解析为数组） |
| `chapters` | JSON 数组 `[{"start":"00:00","title":"…","summary":"…"}]`（阶段九 9.3 契约） |
| `transcript` | 语音转写全文 |

> 说明：结果由 Agent 的 `save_result` Tool 经 `ResultRepository` 写入；路由层不直接写 SQL。
