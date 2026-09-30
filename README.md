# 视频智能分析平台（video-agent）

> 状态：开发中（阶段一：项目初始化）。本 README 为占位版本，最终将按计划书补全 17 项内容。

AI Coding Agent 驱动的视频智能分析与内容工程平台：上传视频 → 解析元数据 → 提取音频/关键帧 → 语音转文字 → AI 生成摘要、关键词、章节，由 Video Analysis Agent 通过 Tool Calling 驱动整条链路。

## 快速开始（当前可用部分）

```bash
# 1. 创建虚拟环境（已完成，见 .venv/）
python -m venv .venv

# 2. 安装依赖
.venv/Scripts/python -m pip install -r requirements.txt

# 3. 启动服务
.venv/Scripts/python run.py

# 4. 健康检查
curl http://127.0.0.1:8000/health
```

## 项目结构（目标）

```
video-agent/
├── app/                # 应用代码（FastAPI 路由、服务、数据库、Agent）
├── tests/              # pytest 测试
├── data/               # 运行时数据（uploads / outputs / database）
├── docs/               # 开发记录与文档
├── tools/              # 辅助脚本
└── run.py              # 启动入口
```

## 待补全

- 架构说明、API 文档、Agent 流程、Tool Calling 说明、测试方法、示例结果、环境变量说明等（见 `计划书/详细步骤.md` 阶段十三）。
