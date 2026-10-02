# 项目难点（project-challenges）

> 整理日期：2026-10-02 · 每条按「问题 → 为什么难 → 解决方案 → 验证」组织，均可在代码与测试中对应核验。
> 配套：[project-summary.md](project-summary.md)（四段式总结）、[development-log.md](development-log.md)（逐阶段过程）。

## 难点一：异步分析链路的状态一致性与失败可诊断

- **问题**：上传 → 提音频 → 转写 → 抽帧 → 三段生成 → 落库，任何一步失败都不能让服务崩，也不能让任务永远卡住。
- **为什么难**：失败点横跨 FFmpeg/opencv/LLM 三层，异常类型完全不同；状态机若允许非法迁移，面板与轮询方就会读到自相矛盾的数据。
- **解决**：任务状态机 `pending → running → success|failed` 只在 Repository 层迁移、非法状态由数据库约束拒绝（`test_database` 覆盖）；失败写入 `error_message` 并由 `/tasks/{id}` 与 `/tasks/{id}/result`（409 附原因）透出；后台任务用顶层 try/except 兜底（`test_tasks` 覆盖转写失败、分析失败、未预期异常三条路径）。
- **验证**：`test_tasks.py` 13 条 + `test_database.py` 7 条，含「失败落 error 且服务存活」。

## 难点二：Agent Tool Calling 循环的健壮性

- **问题**：8 个工具存在硬依赖（转写必须先提音频、生成必须先有转写），LLM 可能乱序调用、传坏参数、返回坏 JSON，或陷入无限循环。
- **为什么难**：LLM 输出不可控，而每步都动真实文件与数据库；错误若直接抛出会杀死任务，若静默吞掉会产出缺损结果。
- **解决**：`TOOL_SCHEMAS` 按依赖顺序在 system prompt 中声明；工具内部做前置校验（缺前置 → 报错而非执行）；**错误回填给 LLM 自纠**（`intermediate_results` + 错误消息继续对话）；`MAX_TOOL_CALLS=10` 硬上限，超限即终止；`save_result` 落库前校验「转写 + 摘要 + 关键词 + 章节」四件齐全，缺一不落。
- **验证**：`test_agent.py` 28 条：乱序/未知工具/坏 JSON/重试 429/超限终止/无 Key 快速失败/落库校验。

## 难点三：LLM 返回的 JSON 不可信 → 修复链

- **问题**：摘要/关键词/章节要求结构化 JSON，但模型常夹代码栅栏、尾逗号、解释性文字，甚至章节 start 为非数字。
- **为什么难**：修复太宽松会放进脏数据（污染数据库），太严格则真实模型输出大量失败。
- **解决**：`parse_llm_json` 分层修复：剥代码栅栏 → 删尾逗号 → 截取首尾大括栏 → **契约校验**（字段类型、`chapters.start` 递增且非负），任何一层失败都显式抛错，不做猜测性修补。
- **验证**：`test_analysis.py` 22 条中 7 条专测修复链与契约拒绝（`repairs_code_fence` / `repairs_trailing_comma_and_prose` / `rejects_bad_chapter_start` 等）。

## 难点四：Windows 中文路径的文件 IO 陷阱

- **问题**：项目根为 `H:\视频分析工程`（中文），`cv2.imwrite` **静默返回 False**，关键帧悄悄缺失而链路「成功」。
- **为什么难**：不抛异常、不报错，只在产物缺失时才被发现——典型的「测试绿、真实环境坏」。
- **解决**：改用 `cv2.imencode` 编码到内存 + `Path.write_bytes`（走 Python Unicode API）；探针实测中文路径输出成功。
- **验证**：`test_keyframes.py::test_extract_keyframes_unicode_output_path` + 真实链路抽帧 10 张。

## 难点五：语音转写降级后，「分片数」的口径诚信

- **问题**：面板要显示「视频分片数」，但 ASR 降级为单段、segments 未持久化，真实可用的分片只有分析产出的 `chapters`。
- **为什么难**：这是一个**产品口径**问题而非技术问题——随手拿 segments 冒充分片数最容易，但数据是假的。
- **解决**：改口径并写进文档：`chapter_count`（最近成功任务的 `chapters` 长度，标注「章节分片」）+ `keyframe_count`（磁盘 `frames/frame_*.jpg` 实数）；面板与 `docs/api.md` §7.2 明确标注取值规则，不虚构。
- **验证**：`test_panel.py::test_videos_list_empty_then_after_upload_and_analyze` 断言 `chapter_count == len(MockAnalysisLLM.MOCK_CHAPTERS)`、`keyframe_count >= 1`。

## 难点六：API Key 的「加密存储 + 不泄露 + 可迁移」

- **问题**：Key 原在 Windows 用户级环境变量（明文），要求迁入面板并加密，且任何接口/页面/日志/仓库都不能出现明文。
- **为什么难**：不能新增依赖；环境变量在 shell 与服务进程间继承不一致；测试进程绝不能读到真实 Key；DPAPI 的边界容易被夸大。
- **解决**：ctypes 直调 Windows DPAPI（`CryptProtectData`）写 `data/secrets/mimo_api_key.bin`，原子写（`.tmp`+`replace`）；接口只回掩码 `sk-****`，`/settings` 只回布尔；优先级 `密钥文件 > 环境变量 > 空`，保存立即生效、删除回退环境变量；`.gitignore` 挡 `data/secrets/*`；`conftest` 把 `KEY_FILE` 指到 `tmp_path`。
- **验证**：`test_api_key.py` 10 条（含「磁盘与响应永不含明文」「monkeypatch 明文不泄漏」），`git check-ignore` 实测命中；DPAPI 边界如实写入 README 已知问题。

## 难点七：可观测性 —— 环形缓冲日志与被 pytest 掩盖的 root level

- **问题**：面板「日志流程」需要实时业务日志，但项目只有启动日志文件；且首版面板 `/logs` 在测试里**一条都读不到**。
- **为什么难**：根因隐蔽——`logging.basicConfig` 在 pytest 进程中因 root 已挂 handler 而失效，root 仍是默认 WARNING，INFO 全部进不了缓冲；日志系统「看起来没问题」。
- **解决**：`log_service.install()` 挂 handler 前先 `root.setLevel(INFO)`（应用本就要求 INFO 日志），幂等安装、1000 条环形缓冲、按级别阈值过滤；不改既有 `logs/launcher.log` 语义。
- **验证**：`test_logs_endpoint_shape_and_limit` / `test_logs_include_task_flow` 等 5 条；真实服务 `/logs?limit=5` 捕获到启动日志。

## 难点八：零依赖控制面板（不做 Swagger 的取舍）

- **问题**：要一个本地 HTML 控制面板，可选做法是改造 `/docs`（Swagger UI）。
- **为什么难**：覆盖 swagger-ui 静态资源既脆弱（FastAPI 升级即破）又做不了上传进度条、轮询刷新、日志着色这类交互；而引入前端框架则违反「第一版不做复杂前端 / 不为堆技术而堆框架」。
- **解决**：自建 `app/static/index.html` 单文件（原生 JS/CSS，无 CDN、无构建），`GET /` 用 `FileResponse` 直出、`/docs` 原样保留；2.5s 轮询带防重入与页面隐藏跳过；文件缺失返回 404 并带中文原因。
- **验证**：`test_panel.py::test_panel_index_serves_html` 断言五大区块文案且**页面中不含任何 `http://`/`https://`**（零外部依赖有测试守卫），`test_docs_still_swagger` 守卫 `/docs` 未被破坏。

## 难点九：144 条测试在「无 Key、无网络、不碰真实数据」下全绿

- **问题**：测试既要覆盖真实链路（上传→任务→结果→面板），又不能依赖 API Key、不能污染 `data/`。
- **为什么难**：lifespan 会真建表、真读配置（含 Key 文件）；隔离做不严，测试会读到真实 Key 或删掉用户数据。
- **解决**：`conftest` 把 UPLOADS/OUTPUTS 目录、SQLite、依赖注入 `get_db`、三处 provider 全部重定向/替换为 mock，`KEY_FILE` 指向 `tmp_path`；测试视频由 ffmpeg 现场生成并复用；改 `load_into_config` 前先核对「无 Key 必失败」用例都显式传空串构造、不读 config。
- **验证**：`pytest -q` → **144 passed, 1 warning**（12.62s，2026-10-02 实测），唯一 warning 为 starlette 上游弃用提示。

## 难点十：开源交付的卫生（历史、密钥、跨平台 CI）

- **问题**：GitHub 上已有网页创建的 LICENSE/README，与本地 17 个提交无共同祖先；真实 Key 掩码曾出现在文档与截图里；CI 跑在 Ubuntu 而 DPAPI 是 Windows 专用。
- **为什么难**：强推会毁掉远端历史；脱敏漏一处就是永久泄露（git 历史可追溯）；CI 红会让徽章永远失败。
- **解决**：`--allow-unrelated-histories` 真合并、README 冲突取本地完整版、**不强推**；Key 掩码片段逐处 scrub（两个专门 commit）、截图用同底色涂盖两处后才入 README；DPAPI 用例加 `skipif(os.name != "nt")`，CI 在 ubuntu + Python 3.13 + ffmpeg 下跑其余用例。
- **验证**：`git ls-remote` 远端与本地一致；全量 144 用例通过；`.env.example` 只含变量名与占位符。
