# AI Coding 开发实录（真实案例）

> 对应《计划书》阶段十三 13.4：记录 AI Coding Agent 驱动本项目的真实开发过程。
> 以下案例均取自 `docs/development-log.md` 的真实时间线，非事后虚构。

## 工作方式总述

每个阶段遵循同一循环（计划书推进规则）：

```
读计划书条目 → 改码前先勘察现有代码 → 设计并写码
→ 单测（无 API Key 的 Mock 路径）→ 真实环境实测
→ 测试失败走「分析 → 定位 → 修改 → 再测试」
→ git commit → 登记 development-log.md → 才进下一阶段
```

AI（编码助手）负责勘察、实现、测试与修复；关键决策与验证结论都要求**真实可验证**——凡是没有实测的结论一律不写进日志。

---

## 案例一（完整 Bug 修复）：`cv2.imwrite` 在中文路径下静默失败

**阶段**：阶段八（关键帧提取，2026-10-01）。这是"单测全绿、真实环境却炸"的典型问题。

### 1. 现象

8 条单元测试全部通过（临时目录为纯 ASCII 路径），但第一次在真实环境跑抽帧即抛：

```
KeyframeExtractionError: 写入关键帧失败：H:\视频分析工程\video-agent\data\outputs\__kfcheck__\frames\frame_0001.jpg
```

### 2. 分析与定位

- 单测用 pytest 的 `tmp_path`（如 `C:\Users\...\AppData\Local\Temp\...`，**全 ASCII**），所以从未复现。
- 真实项目根在 `H:\视频分析工程\`——**中文路径**。
- `cv2.imwrite()` 走 C 底层窄字符 `fopen`，在中文 Windows 路径上打开失败并**只返回 False、不抛异常**；代码把 False 翻译成 `写入关键帧失败`。
- 结论：`cv2.imwrite` 的中文路径兼容性问题（OpenCV 官方 Windows 已知行为），不是视频或帧数据问题。

### 3. 修改

Unicode 安全的两步写法替代：

```python
# 修复前
ok = cv2.imwrite(str(out_path), frame)
if not ok:
    raise KeyframeExtractionError(f"写入关键帧失败：{out_path}")

# 修复后（cv2.imencode 编码到内存 → Path.write_bytes 走 Python Unicode API）
ok, buf = cv2.imencode(".jpg", frame)
if not ok:
    raise KeyframeExtractionError(f"JPEG 编码失败：{out_path}")
try:
    out_path.write_bytes(buf.tobytes())
except OSError as exc:
    raise KeyframeExtractionError(f"写入关键帧失败：{out_path}") from exc
```

### 4. 回归测试

补 `test_extract_keyframes_unicode_output_path`：输出根用 `tmp_path / "视频输出"`（中文目录），断言帧落盘且 JPEG 魔数正确——把这个坑永久钉进测试。

### 5. 再测试与真实验证

- 全量 `pytest`：**61 passed**（含新回归测试）；
- 真实环境重跑：默认间隔抽 1 帧 / 1 秒间隔抽 2 帧、`jpeg-magic=True`、文件 13411/12914 bytes、清理成功；
- 结论与教训记入 development-log：**"真实路径验证不可省"——单测的临时目录掩盖了部署环境差异。**

---

## 案例二（较轻）：JSON 修复链漏掉数组层级

**阶段**：阶段九（AI 内容分析）。

- 现象：`test_parse_llm_json_repairs_unclosed_object` 失败，报错 `Expecting ',' delimiter: line 1 column 63`——LLM 返回**被截断的章节 JSON**（对象里嵌着未闭合的 `[` 数组）。
- 定位：`_extract_first_object` 只跟踪 `{}` 配平，不跟踪 `[]`；截断对象补上 `}` 后，内层 `[` 仍未闭合，`json.loads` 仍失败。
- 修改：改成栈式跟踪 `{}`/`[]`（`stack.append("}")` / `stack.append("]")`，closer 匹配 `stack[-1]` 才 pop），截断且不在字符串字面量中时按栈补齐缺失的闭合符。
- 再测试：全量 **84 passed**。
- 启示：修复链必须覆盖"截断发生在嵌套结构任意层级"的真实 LLM 输出形态，而不是只测理想的顶层截断。

---

## 案例三：接线疏漏（改核心函数签名后测试红）

**阶段**：阶段十（Agent 接入）。

- `task_service.analyze_video` 增加 `task_id` 关键字参数后，test_tasks 的 spy 测试仍按旧签名 `spy(session, video)` 被调用 → 首跑 6 failed，且伴随一处漏 `import config` 的 NameError。
- 修复：spy/boom 都改为 `spy(session, video, task_id=None)`，补 import，重跑全绿。
- 启示：AI Coding 中"修改共享函数签名"必须 grep 全部调用点与 monkeypatch 接缝（本项目 grep 到 3 处）。

---

## 通用经验（本项目沉淀）

1. **Mock 与真实环境都要跑**：Mock 保证无 Key 可测，真实环境才暴露路径编码、进程管道、端口占用等问题。
2. **测试失败严格走四步**：分析 → 定位 → 修改 → 再测试，禁止跳过或改断言凑绿。
3. **Windows 特有坑**：GBK 控制台中文乱码仅是显示问题；`CREATE_NO_WINDOW` 避免弹窗；中文路径需 Unicode 安全 API（`Path.write_bytes`、`encoding="utf-8"` 的 subprocess）。
4. **每个 bug 留回归测试**，否则等号两头（修复与文档）都会腐烂。
