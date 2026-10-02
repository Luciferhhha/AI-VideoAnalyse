# Video Analysis Agent 说明

> 对应《计划书》阶段十三 13.3：Agent 工作流程、Tool Calling 说明、状态管理（代码：`app/agent/`）。

## 1. 工作流程

Agent 是一次分析任务的**决策者**：由 LLM 在每一步选择一个 Tool，执行后把结果回传，循环直到分析完成。

```
run_analysis_task(task_id)                 # task_service（AGENT_DRIVER=agent，默认）
  └─ AgentContext(video, session, task_id)
       └─ run_agent(context) → AgentState
            循环（最多 MAX_TOOL_CALLS = 10 次）：
              ① 构造 messages = [系统提示词, 各步 Tool 结果回填]
              ② LLM.decide(messages, TOOL_SCHEMAS)
                   ├─ 返回 tool_calls → 取第 1 个 → execute_tool
                   │     ├─ 成功：state.intermediate_results[name] = result
                   │     └─ 失败：state.errors 记录，回填错误给 LLM（可重试或改道）
                   └─ 返回 content（纯文本）→ 视为最终回答，结束
              ③ save_result 成功 → 正常结束
              ④ 超过 10 次 → AgentError("工具调用次数超限…") 终止
  结果落库 analysis_results → 任务 success / failed
```

- **每步只调一个 Tool**（取 LLM 返回的第 1 个），保证顺序可观测、状态可回填。
- 正常完成路径固定 8 步（见下表）；顺序由提示词约束，但 LLM 可自行纠错（错误回填后允许换 Tool 或重试）。

## 2. Tool Calling 说明

8 个 Tool 注册于 `app/agent/tools.py`（`ToolSpec(name, parameters, handler)`），`TOOL_SCHEMAS` 为 OpenAI function calling 格式，描述取自 `app/agent/prompts.py::TOOL_DESCRIPTIONS`。

| # | Tool | 输入（JSON Schema） | 输出（回填 intermediate_results） | 依赖/备注 |
|---|---|---|---|---|
| 1 | `get_video_info` | `{}` | 元数据 dict（时长/分辨率/帧率/编码…） | 无需参数，第一步 |
| 2 | `extract_audio` | `{}` | `{"audio_path": "data/outputs/{id}/audio.wav"}` | FFmpeg 提取 16kHz 单声道 wav |
| 3 | `transcribe_audio` | `{audio_path?}` | `{"text", "segments"}` | 默认取第 2 步的 audio_path；缺前序步骤 → `ToolError` |
| 4 | `extract_keyframes` | `{interval_seconds?}`（默认 5） | `{"count", "frames":[{timestamp, filepath}]}` | OpenCV 每 N 秒一帧 |
| 5 | `generate_summary` | `{}` | `{"summary"}` | 必须先有转写文本 |
| 6 | `generate_keywords` | `{}` | `{"keywords": [...]}` | 必须先有转写文本 |
| 7 | `generate_chapters` | `{}` | `{"chapters":[{start,title,summary}]}` | 用 video.duration 校准 |
| 8 | `save_result` | `{}` | `{"saved": true, "task_id": N}` | 依赖 3+5+6+7 齐全；写 `analysis_results`，最后一步 |

执行器 `execute_tool(name, arguments, context, state)` 的保证：

- 未知工具 → `未知工具：{name}`；
- handler 抛出的任何异常统一包成 `{name} 执行失败：{exc}` 的错误结果（不崩 Agent）；
- 输出必须是可 JSON 序列化的 dict，否则报错；
- 成功才写 `state.intermediate_results[name]`。

LLM 侧：`AgentLLM.decide(messages, tools) -> AgentDecision`（抽象）。`MimoAgentLLM` 走 mimo 原生 Tool Calling（`tools` 参数 + `message.tool_calls`，429/5xx/超时重试、Retry-After 优先、封顶 30s；未配 Key 报 `未配置 MIMO_API_KEY…无法调用 mimo Agent API。`）；`MockAgentLLM` 无 Key 场景返回**脚本化的 8 步固定序列**，测试与真实环境均可全链路跑通。工厂 `get_agent_llm()` 每次读 `config.AGENT_PROVIDER`。

## 3. 状态管理（AgentState）

```python
AgentState(
    video_id,                 # 分析对象
    task_id,                  # 关联任务（save_result 写库必需）
    current_step,             # 当前阶段标记（init / 工具名 / done…）
    tool_calls=[],            # 已发起的调用记录 ToolCall(id, name, arguments)
    intermediate_results={},  # 工具名 → 输出，后续 Tool 从这里取前序产物
    errors=[],                # 错误记录（回填给 LLM，也可追溯）
    final_answer=None,        # LLM 最终文本总结
)
```

- `AgentContext(video, session, task_id)` 提供依赖（SQLAlchemy Session 给 `save_result`）。
- **全程落日志**（阶段十 10.5 / 阶段十二 12.2）：`Agent 启动 video_id=… task_id=… max_tool_calls=10` → 每次 `Agent 工具调用 tool=… args={}` → `Agent 完成 tool_calls=8 errors=0`；超限与工具失败为 WARNING/ERROR。
- **终止条件**：① LLM 返回纯文本（视为完成）；② `save_result` 成功；③ 超过 `MAX_TOOL_CALLS = 10` → `AgentError`（禁止无限调用，10.4）；④ LLM/网络不可恢复错误。
- 失败落库：`run_analysis_task` 捕获一切异常 → 任务 `failed` + `error_message`，服务不崩溃（阶段五 5.3）。

## 4. 与任务链路的关系

| `AGENT_DRIVER` | 链路 | 用途 |
|---|---|---|
| `agent`（默认） | Agent 循环驱动 8 Tool（10.6 接入阶段五异步任务） | 项目核心链路 |
| `direct` | 阶段九直连：`analyze_video` 顺序调用同一组 Service | 对照与降级 |

两条链路共用 Service 层与输出契约，结果同为 `summary/keywords/chapters/transcript` 四列。
