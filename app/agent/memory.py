from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.agent import mysql_memory_store
from app.agent.event_extractor import EventExtractor
from app.agent.memory_repositories import EventRepository, SummaryRepository
from app.llm.deepseek_client import DeepSeekClient


# Three complete turns keeps the prompt focused while preserving the last
# question/answer pair for follow-up requests.
RECENT_TURN_COUNT = 3
RECENT_MESSAGE_LIMIT = RECENT_TURN_COUNT * 2
RECENT_OPERATION_LIMIT = 3
SUMMARY_MAX_CHARS = 6000


class Message(BaseModel):
    role: str
    content: str
    intent: Optional[str] = None
    evidence_sources: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class OperationRecord(BaseModel):
    standalone_question: str
    intent: Optional[str] = None
    selected_tool: Optional[str] = None
    tool_args: dict = Field(default_factory=dict)
    queries: list[str] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)
    retrieval_grade: Optional[str] = None
    retrieval_score: Optional[float] = None
    self_check_result: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ArtifactRecord(BaseModel):
    name: str
    artifact_type: str = "file"
    file_path: str
    summary: str = ""
    content_sha256: str = ""
    updated_at: str = ""


class ConversationMemory(BaseModel):
    conversation_id: str
    messages: list[Message] = Field(default_factory=list)
    operations: list[OperationRecord] = Field(default_factory=list)
    # ``summary`` remains available to older callers.  It is the legacy value
    # when present, otherwise a readable representation of semantic memory.
    summary: str = ""
    semantic_summary: dict[str, Any] = Field(default_factory=dict)
    task_summary: dict[str, Any] = Field(default_factory=dict)
    confirmed_facts: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)


_CONVERSATIONS: dict[str, ConversationMemory] = {}


def get_or_create_memory(conversation_id: Optional[str] = None) -> ConversationMemory:
    new_id = conversation_id or uuid4().hex
    if not mysql_memory_store.session_exists(new_id):
        mysql_memory_store.create_session(new_id, "New session")

    session = mysql_memory_store.get_session(new_id) or {}
    mysql_memory_store.reconcile_session_artifacts(new_id)
    summary_repository = SummaryRepository()
    semantic_raw = summary_repository.get_semantic_summary(new_id)
    task_raw = summary_repository.get_task_summary(new_id)
    rows = mysql_memory_store.list_messages(new_id, limit=RECENT_MESSAGE_LIMIT)
    operation_rows = mysql_memory_store.list_operations(new_id, limit=RECENT_OPERATION_LIMIT)
    artifact_rows = mysql_memory_store.list_session_artifacts(new_id, limit=20)
    semantic_summary = _parse_summary_object(semantic_raw)
    task_summary = _parse_summary_object(task_raw)
    memory = ConversationMemory(
        conversation_id=new_id,
        summary=str(session.get("summary") or semantic_raw or ""),
        semantic_summary=semantic_summary,
        task_summary=task_summary,
        messages=[_message_from_row(row) for row in rows],
        operations=[_operation_from_row(row) for row in operation_rows],
        artifacts=[_artifact_from_row(row) for row in artifact_rows],
    )
    _CONVERSATIONS[new_id] = memory
    return memory


def append_message(
    conversation_id: str,
    role: str,
    content: str,
    intent: Optional[str] = None,
    evidence_sources: Optional[list[str]] = None,
) -> ConversationMemory:
    memory = get_or_create_memory(conversation_id)
    mysql_memory_store.add_message(
        memory.conversation_id,
        role,
        content,
        intent=intent,
        evidence_sources=evidence_sources or [],
    )
    if role == "assistant":
        # A failure in the optional memory services must never affect delivery
        # of a completed assistant answer.
        _extract_events_after_assistant_response(memory.conversation_id, content)
        update_memory_summary(memory.conversation_id)
    _CONVERSATIONS.pop(memory.conversation_id, None)
    return get_or_create_memory(memory.conversation_id)


def append_operation(
    conversation_id: str,
    standalone_question: str,
    intent: Optional[str] = None,
    selected_tool: Optional[str] = None,
    tool_args: Optional[dict] = None,
    queries: Optional[list[str]] = None,
    evidence_sources: Optional[list[str]] = None,
    retrieval_grade: Optional[str] = None,
    retrieval_score: Optional[float] = None,
    self_check_result: Optional[dict] = None,
) -> ConversationMemory:
    memory = get_or_create_memory(conversation_id)
    mysql_memory_store.add_operation(
        memory.conversation_id,
        standalone_question,
        intent=intent,
        selected_tool=selected_tool,
        tool_args=tool_args or {},
        queries=queries or [],
        evidence_sources=evidence_sources or [],
        retrieval_grade=retrieval_grade,
        retrieval_score=retrieval_score,
        self_check_result=self_check_result or {},
    )
    _CONVERSATIONS.pop(memory.conversation_id, None)
    return get_or_create_memory(memory.conversation_id)


def reset_memory(conversation_id: str) -> ConversationMemory:
    if not mysql_memory_store.session_exists(conversation_id):
        mysql_memory_store.create_session(conversation_id, "New session")
    mysql_memory_store.reset_session(conversation_id)
    SummaryRepository().invalidate(conversation_id)
    EventRepository().invalidate(conversation_id)
    _CONVERSATIONS.pop(conversation_id, None)
    return get_or_create_memory(conversation_id)


def delete_memory(conversation_id: str) -> bool:
    _CONVERSATIONS.pop(conversation_id, None)
    deleted = mysql_memory_store.delete_session(conversation_id)
    SummaryRepository().invalidate(conversation_id)
    EventRepository().invalidate(conversation_id)
    return deleted


def list_session_summaries(limit: int = 20) -> list[dict]:
    return mysql_memory_store.list_sessions(limit=limit)


def list_session_messages(conversation_id: str, limit: int = 50) -> list[dict]:
    return mysql_memory_store.list_messages(conversation_id, limit=limit)


def update_memory_summary(conversation_id: str) -> str:
    """Refresh split summaries atomically from the latest task-relevant state.

    No text-concatenation fallback is used: an unavailable or malformed LLM
    result leaves both existing structured summaries untouched.
    """
    repository = SummaryRepository()
    previous_semantic = _parse_summary_object(repository.get_semantic_summary(conversation_id))
    previous_task = _parse_summary_object(repository.get_task_summary(conversation_id))
    recent_messages = [
        _message_from_row(row)
        for row in mysql_memory_store.list_messages(conversation_id, limit=RECENT_MESSAGE_LIMIT)
    ]
    if not recent_messages:
        return _json_summary_text(previous_semantic)

    task_state = _compact_react_task(mysql_memory_store.get_latest_react_task(conversation_id))
    current_question = str((task_state or {}).get("question") or recent_messages[-1].content)
    try:
        important_events = EventRepository().get_important_events(
            conversation_id,
            query=current_question,
            limit=10,
            min_importance=0.7,
        )
    except Exception:
        important_events = []
    result = _llm_update_split_summaries(
        previous_semantic,
        previous_task,
        recent_messages,
        task_state,
        important_events=important_events,
    )
    if result is None:
        return _json_summary_text(previous_semantic)

    semantic_summary, task_summary = result
    repository.update_semantic_summary(conversation_id, _json_summary_text(semantic_summary))
    repository.update_task_summary(conversation_id, _json_summary_text(task_summary))
    _CONVERSATIONS.pop(conversation_id, None)
    return _json_summary_text(semantic_summary)


def _llm_update_split_summaries(
    previous_semantic: dict[str, Any],
    previous_task: dict[str, Any],
    recent_messages: list[Message],
    react_task_state: dict[str, Any] | None,
    important_events: list[dict[str, Any]] | None = None,
    client: DeepSeekClient | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    client = client or DeepSeekClient()
    if not client.available:
        return None
    data = client.json_chat(
        [
            {
                "role": "system",
                "content": (
                    "You maintain structured agent memory. Return JSON only, exactly with "
                    "semantic_summary and task_summary object fields. semantic_summary contains "
                    "stable cross-task user preferences, projects, and environment facts. "
                    "task_summary contains only current-session goal, completed work, issue, failed attempts, and next steps. "
                    "Do not invent facts."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "previous_semantic_summary": previous_semantic,
                        "previous_task_summary": previous_task,
                        "recent_messages": [message.model_dump() for message in recent_messages],
                        "current_react_task_state": react_task_state or {},
                        "important_events": important_events or [],
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ]
    )
    return _validate_split_summary(data)


def _validate_split_summary(value: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Strict schema boundary for LLM summary output."""
    if not isinstance(value, dict):
        return None
    semantic = value.get("semantic_summary")
    task = value.get("task_summary")
    if not isinstance(semantic, dict) or not isinstance(task, dict):
        return None
    try:
        semantic_text = json.dumps(semantic, ensure_ascii=False, default=str)
        task_text = json.dumps(task, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None
    if len(semantic_text) > SUMMARY_MAX_CHARS or len(task_text) > SUMMARY_MAX_CHARS:
        return None
    return semantic, task


def _extract_events_after_assistant_response(conversation_id: str, assistant_response: str) -> None:
    try:
        messages = mysql_memory_store.list_messages(conversation_id, limit=RECENT_MESSAGE_LIMIT)
        latest_user = next(
            (str(row.get("content", "")) for row in reversed(messages) if row.get("role") == "user"),
            "",
        )
        task = mysql_memory_store.get_latest_react_task(conversation_id) or {}
        task_state = task.get("state") if isinstance(task.get("state"), dict) else {}
        tool_results = list(task_state.get("react_steps") or [])[-5:]
        EventExtractor().extract_and_store(
            conversation_id,
            user_message=latest_user,
            assistant_response=assistant_response,
            tool_results=tool_results,
        )
    except Exception:
        # Event persistence is an optimization, never a chat-path dependency.
        return


def _parse_summary_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        # Legacy free-text summaries are kept compatible but are not claimed as
        # structured facts.
        return {}
    return data if isinstance(data, dict) else {}


def _json_summary_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _compact_react_task(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if not task:
        return None
    state = task.get("state") if isinstance(task.get("state"), dict) else {}
    steps = list(state.get("react_steps") or [])[-5:]
    return {
        "task_id": task.get("task_id"),
        "status": task.get("status"),
        "question": state.get("question"),
        "task_status": state.get("task_status"),
        "steps": steps,
    }


def _message_from_row(row: dict[str, Any]) -> Message:
    created = row.get("created_at")
    return Message(
        role=str(row["role"]),
        content=str(row["content"]),
        intent=row.get("intent"),
        evidence_sources=list(row.get("evidence_sources") or []),
        created_at=created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
    )


def _operation_from_row(row: dict[str, Any]) -> OperationRecord:
    created = row.get("created_at")
    return OperationRecord(
        standalone_question=str(row["standalone_question"]),
        intent=row.get("intent"),
        selected_tool=row.get("selected_tool"),
        tool_args=dict(row.get("tool_args") or {}),
        queries=list(row.get("queries") or []),
        evidence_sources=list(row.get("evidence_sources") or []),
        retrieval_grade=row.get("retrieval_grade"),
        retrieval_score=float(row["retrieval_score"]) if row.get("retrieval_score") is not None else None,
        self_check_result=dict(row.get("self_check_result") or {}),
        created_at=created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
    )


def _artifact_from_row(row: dict[str, Any]) -> ArtifactRecord:
    updated = row.get("updated_at")
    return ArtifactRecord(
        name=str(row.get("name", "")),
        artifact_type=str(row.get("artifact_type", "file")),
        file_path=str(row.get("file_path", "")),
        summary=str(row.get("summary") or ""),
        content_sha256=str(row.get("content_sha256") or ""),
        updated_at=updated.isoformat() if hasattr(updated, "isoformat") else str(updated or ""),
    )
