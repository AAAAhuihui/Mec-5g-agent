from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str
    intent: Optional[str] = None
    evidence_sources: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ConversationMemory(BaseModel):
    conversation_id: str
    messages: list[Message] = Field(default_factory=list)
    summary: str = ""
    confirmed_facts: list[str] = Field(default_factory=list)


_CONVERSATIONS: dict[str, ConversationMemory] = {}


def get_or_create_memory(conversation_id: Optional[str] = None) -> ConversationMemory:
    if conversation_id and conversation_id in _CONVERSATIONS:
        return _CONVERSATIONS[conversation_id]

    new_id = conversation_id or uuid4().hex
    memory = ConversationMemory(conversation_id=new_id)
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
    memory.messages.append(
        Message(
            role=role,
            content=content,
            intent=intent,
            evidence_sources=evidence_sources or [],
        )
    )
    # MVP 只保留最近 12 条消息，避免上下文无限膨胀。
    memory.messages = memory.messages[-12:]
    _refresh_summary(memory)
    return memory


def reset_memory(conversation_id: str) -> ConversationMemory:
    memory = ConversationMemory(conversation_id=conversation_id)
    _CONVERSATIONS[conversation_id] = memory
    return memory


def _refresh_summary(memory: ConversationMemory) -> None:
    recent = memory.messages[-6:]
    memory.summary = "\n".join(f"{message.role}: {message.content[:160]}" for message in recent)
