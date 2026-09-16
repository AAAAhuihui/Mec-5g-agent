from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.agent.memory import (
    delete_memory,
    get_or_create_memory,
    list_session_messages,
    list_session_summaries,
)
from app.schemas import (
    CreateSessionResponse,
    DeleteSessionResponse,
    SessionItem,
    SessionListResponse,
    SessionMessageItem,
    SessionMessagesResponse,
)


router = APIRouter(tags=["sessions"])


def _to_str_datetime(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


@router.post("/sessions", response_model=CreateSessionResponse)
def create_session() -> CreateSessionResponse:
    conversation_id = uuid4().hex
    get_or_create_memory(conversation_id)
    return CreateSessionResponse(conversation_id=conversation_id)


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(limit: int = 20) -> SessionListResponse:
    sessions = [
        SessionItem(
            id=str(row["id"]),
            title=str(row["title"]),
            created_at=_to_str_datetime(row["created_at"]),
            updated_at=_to_str_datetime(row["updated_at"]),
            message_count=int(row["message_count"]),
        )
        for row in list_session_summaries(limit=limit)
    ]
    return SessionListResponse(sessions=sessions)


@router.get("/sessions/{conversation_id}/messages", response_model=SessionMessagesResponse)
def get_session_messages(conversation_id: str, limit: int = 50) -> SessionMessagesResponse:
    messages = [
        SessionMessageItem(
            id=int(row["id"]),
            session_id=str(row["session_id"]),
            role=str(row["role"]),
            content=str(row["content"]),
            intent=row.get("intent"),
            evidence_sources=list(row.get("evidence_sources") or []),
            created_at=_to_str_datetime(row["created_at"]),
        )
        for row in list_session_messages(conversation_id, limit=limit)
    ]
    return SessionMessagesResponse(conversation_id=conversation_id, messages=messages)


@router.delete("/sessions/{conversation_id}", response_model=DeleteSessionResponse)
def delete_session(conversation_id: str) -> DeleteSessionResponse:
    deleted = delete_memory(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return DeleteSessionResponse(conversation_id=conversation_id, deleted=True)
