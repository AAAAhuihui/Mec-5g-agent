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
    task_id: str = ""
    task_status: str = "completed"
    standalone_question: str
    answer: str
    intent: str
    retrieval_grade: str
    self_check: dict[str, Any]
    evidence: list[EvidenceItem]
    approval: dict[str, Any] = Field(default_factory=dict)
    task_timing: dict[str, Optional[int]] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)


class CmdApprovalRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    approved: bool


class CmdApprovalResponse(BaseModel):
    approval_id: str
    status: str
    command: str
    working_dir: str
    expires_at: str
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    task_id: str = ""
    task_status: str = ""
    answer: str = ""
    next_approval: dict[str, Any] = Field(default_factory=dict)
    task_timing: dict[str, Optional[int]] = Field(default_factory=dict)


class FileApprovalRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    approved: bool


class FileApprovalResponse(BaseModel):
    approval_id: str
    approval_type: str = "file_write"
    status: str
    path: str
    rationale: str
    expires_at: str
    content_preview: str = ""
    content_sha256: str = ""
    content_chars: int = 0
    success: bool = False
    error: Optional[str] = None
    task_id: str = ""
    task_status: str = ""
    answer: str = ""
    next_approval: dict[str, Any] = Field(default_factory=dict)
    task_timing: dict[str, Optional[int]] = Field(default_factory=dict)


class SessionItem(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class SessionMessageItem(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    intent: Optional[str] = None
    evidence_sources: list[str] = Field(default_factory=list)
    created_at: str


class CreateSessionResponse(BaseModel):
    conversation_id: str


class DeleteSessionResponse(BaseModel):
    conversation_id: str
    deleted: bool


class SessionListResponse(BaseModel):
    sessions: list[SessionItem]


class SessionMessagesResponse(BaseModel):
    conversation_id: str
    messages: list[SessionMessageItem]
