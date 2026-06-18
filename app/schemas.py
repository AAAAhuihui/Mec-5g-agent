from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class IngestRequest(BaseModel):
    source_dir: str = Field(default="data/raw_docs", description="Markdown/txt 文档目录")


class IngestResponse(BaseModel):
    success: bool
    message: str
    chunks: int


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = Field(default=None, description="多轮会话 ID，不传则新建会话")
    question: str = Field(min_length=1, description="用户问题")
    include_trace: bool = Field(default=False, description="是否返回可审计推理轨迹")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "conversation_id": None,
                    "question": "MEPM 在 MEC 系统中的作用是什么？",
                    "include_trace": False,
                },
                {
                    "conversation_id": "demo-session",
                    "question": "NEF 返回成功，但流量没有进入 MEC APP，可能是什么原因？",
                    "include_trace": True,
                },
            ]
        }
    )


class EvidenceItem(BaseModel):
    source: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    conversation_id: str
    standalone_question: str
    answer: str
    intent: str
    retrieval_grade: str
    self_check: dict[str, Any]
    evidence: list[EvidenceItem]
    trace: dict[str, Any] = Field(default_factory=dict)
