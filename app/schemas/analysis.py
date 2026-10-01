"""LLM 分析结果的结构化契约（阶段九 9.2 校验 / 9.3 章节格式）。

LLM 必须返回纯 JSON 对象；`parse_llm_json`（analysis_service）负责解析、
修复与本模块的 Pydantic 校验，非法内容抛 `AnalysisServiceError`。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SummaryPayload(BaseModel):
    """generate_summary 的输出契约：{"summary": str}。"""

    summary: str = Field(min_length=1)


class KeywordsPayload(BaseModel):
    """generate_keywords 的输出契约：{"keywords": [str, ...]}。"""

    keywords: list[str]


class ChapterItem(BaseModel):
    """9.3 章节条目：{"start": "00:00", "title": str, "summary": str}。

    start 为 MM:SS（允许 M:SS / MM:SS / H:MM:SS，分钟位可超过 59 以覆盖
    长视频），title 与 summary 非空。
    """

    start: str = Field(pattern=r"^\d{1,3}:\d{2}(:\d{2})?$")
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class ChaptersPayload(BaseModel):
    """generate_chapters 的输出契约：{"chapters": [ChapterItem, ...]}。"""

    chapters: list[ChapterItem]
