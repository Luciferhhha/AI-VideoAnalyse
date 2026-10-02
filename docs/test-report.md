# 测试报告（docs/test-report.md）

> 阶段十一 11.3：一次全量 pytest 的真实结果记录。
> 生成方式：在 `video-agent/` 下执行 `python -m pytest -q`（**无 API Key 环境**）。
>
> **时效说明**：本报告是**阶段十一（2026-10-01）的快照（115 passed）**，其"按文件分布/合计"保持当时原貌不再回填。
> 后续增量（面板、面板 API Key 管理）已使全量达到 **144 passed**（2026-10-02，见 `docs/development-log.md` 与 `README.md` §13）。

## 运行环境

| 项 | 值 |
| --- | --- |
| 日期 | 2026-10-01 |
| 平台 | Windows（中文区域），项目根含中文路径 `H:\视频分析工程` |
| Python | 3.13.2（`.venv`） |
| 关键依赖 | fastapi 0.142.2 / sqlalchemy 2.1.1 / httpx 0.28.1 / opencv 5.0.0 / pytest 9.1.1 |
| 外部工具 | ffmpeg / ffprobe（PATH 可用） |
| API Key | **未设置**（`MIMO_API_KEY`、`TRANSCRIPTION_PROVIDER`、`ANALYSIS_PROVIDER`、`AGENT_PROVIDER`、`AGENT_DRIVER` 全部显式清空，走默认配置 + 测试 fixture 内 mock） |

## 全量结果

```
115 passed, 1 warning in 17.14s
```

- **115 passed / 0 failed**（无 API Key，11.2 达成）。
- 唯一 warning 为已知第三方提示：`StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`（starlette 上游弃用提示，非本项目代码）。
- 最慢 5 条均为集成链路测试（`test_analysis_failure_marks_task_failed_with_reason` 0.95s 等），全部 < 1s。

## 按文件分布

| 文件 | 数量 | 覆盖内容 |
| --- | ---: | --- |
| tests/test_agent.py | 28 | Agent Tool 单测、MockLLM 流程、超限/异常、MimoAgentLLM 重试与解析、工厂 |
| tests/test_analysis.py | 22 | 三个生成函数、JSON 修复链、消息构造、MimoAnalysisLLM、keyframe 选择 |
| tests/test_transcription.py | 20 | Mock 契约、工厂三态、Mimo 请求/重试/超时/结构异常、格式支持 |
| tests/test_tasks.py | 9 | 任务创建 202、pending→running→success、失败落 error、Agent/转写失败路径、404 |
| tests/test_keyframes.py | 8 | 正常/短视频/不存在/非视频/非法参数/路径穿越/重跑清理/中文路径 |
| tests/test_database.py | 7 | 建表、Video CRUD、任务状态机、非法状态、外键、级联删除 |
| tests/test_api.py | 6 | /health、lifespan 建表、非整型 id 422、缺文件 422（非法输入） |
| tests/test_audio.py | 6 | 正常提取/防冲突/无音频流/不存在/FFmpeg 缺失/失败清理 |
| tests/test_upload.py | 5 | 合法上传、坏扩展名、伪造视频、超大文件、404 |
| tests/test_video.py | 4 | ffprobe 信息、不存在、非视频、FFmpeg 缺失 |
| **合计** | **115** | |

## 十类测试覆盖对照（11.1）

| # | 类别 | 覆盖位置 | 状态 |
| --- | --- | --- | :---: |
| 1 | 视频解析 | test_video.py（4） | ✅ |
| 2 | 数据库 | test_database.py（7） | ✅ |
| 3 | 上传 | test_upload.py（5） | ✅ |
| 4 | Task 创建 | test_tasks.py `test_analyze_returns_task_id_immediately` | ✅ |
| 5 | Task 状态 | test_tasks.py 状态流转 / 失败落 error（+ test_database 状态机） | ✅ |
| 6 | API | test_api.py（6）+ 各文件 404/422 断言 | ✅ |
| 7 | Agent Tool | test_agent.py Tool 单测 12 条（依赖顺序、参数校验、save_result） | ✅ |
| 8 | Agent 异常 | test_agent.py 超限 / Tool 失败回传 / 未知工具 / 无 Key（4 条） | ✅ |
| 9 | 非法输入 | test_api 422 ×3、test_upload 坏扩展名/伪造/超大、非法 interval/video_id、坏章节 start | ✅ |
| 10 | 文件不存在 | test_video / test_audio / test_keyframes / test_transcription / test_tasks 的 not-found 路径 | ✅ |

## 复现方式

```powershell
cd H:\视频分析工程\video-agent
# 无需任何 API Key，直接：
.venv\Scripts\python.exe -m pytest -q
```

单文件示例：`.venv\Scripts\python.exe -m pytest tests/test_agent.py -q`

## 最终全量（阶段十五交付，2026-10-02）

```
144 passed, 1 warning in 12.62s
```

- 运行环境同上表（Windows / Python 3.13 / 无 API Key）；warning 仍为同一条 starlette `TestClient` 弃用提示（第三方）。
- 相比阶段十一快照的 115 条，增量 **+29**：结果查询接口 +4（test_tasks 9→13）、控制面板 +15（test_panel）、面板 API Key 管理 +10（test_api_key，Windows 专用 7 条在非 Windows 平台自动 skip）。

| 文件 | 数量 | 说明 |
| --- | ---: | --- |
| tests/test_agent.py | 28 | 不变 |
| tests/test_analysis.py | 22 | 不变 |
| tests/test_transcription.py | 20 | 不变 |
| tests/test_tasks.py | 13 | +4：`GET /tasks/{id}/result` 的 200 / 409 / 404 与转写落库 |
| tests/test_keyframes.py | 8 | 不变 |
| tests/test_database.py | 7 | 不变 |
| tests/test_api.py | 6 | 不变 |
| tests/test_audio.py | 6 | 不变 |
| tests/test_upload.py | 5 | 不变 |
| tests/test_video.py | 4 | 不变 |
| tests/test_panel.py | 15 | 新增：页面五大区块与零外部依赖、404 兜底、`/docs` 仍 Swagger、视频/任务列表、`/logs` 结构与 limit/level、`/settings` 结构与 Key 不泄漏 |
| tests/test_api_key.py | 10 | 新增：DPAPI 往返、掩码规则、磁盘与响应永不含明文、保存立即生效、空值/掩码值 400、删除回退环境变量、加载优先级 |
| **合计** | **144** | |

## 备注

- 测试隔离：`conftest.py` 的 `client` fixture 把上传/输出目录与 SQLite 重定向到 `tmp_path`，并把转写/分析/Agent provider 切到 `mock`；真实 `data/` 不被测试触碰。
- 两个 session fixture 用 ffmpeg 现场生成 `tests/fixtures/*.mp4`（已被 .gitignore 忽略，首次运行自动创建并复用）。
- 真实 API（mimo）联网回归待填 Key 后另行执行，不在本报告范围（用户要求初版完成后再配 Key）。
