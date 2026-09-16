from __future__ import annotations

from datetime import datetime, timezone

from app.agent import mysql_memory_store
from app.agent.memory_repositories import EventRepository, SummaryRepository
from app.agent.redis_cache_repository import RedisCacheRepository


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int) -> None:
        self.data[key] = value

    def delete(self, key: str) -> None:
        self.data.pop(key, None)

    def incr(self, key: str) -> int:
        value = int(self.data.get(key, "0")) + 1
        self.data[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> None:
        return None


def test_summary_repository_uses_cache_aside(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        mysql_memory_store,
        "get_session",
        lambda session_id: calls.append(session_id)
        or {"semantic_summary": '{"environment":{"os":"Windows"}}', "task_summary": "{}"},
    )
    repository = SummaryRepository(RedisCacheRepository(client=_FakeRedis()))

    first = repository.get_semantic_summary("session-1")
    second = repository.get_semantic_summary("session-1")

    assert first == second
    assert calls == ["session-1"]


def test_important_events_are_cached_and_invalidated_by_new_event(monkeypatch) -> None:
    calls = []
    event = {
        "event_type": "TOOL_FAILURE",
        "content": "docker build permission denied",
        "importance": 0.95,
        "created_time": datetime.now(timezone.utc),
    }
    monkeypatch.setattr(
        mysql_memory_store,
        "list_recent_events",
        lambda session_id, limit: calls.append((session_id, limit)) or [dict(event)],
    )
    repository = EventRepository(RedisCacheRepository(client=_FakeRedis()))

    first = repository.get_important_events("session-1", query="docker build")
    second = repository.get_important_events("session-1", query="docker build")

    assert first == second
    assert calls == [("session-1", 100)]
    repository.invalidate("session-1")
    repository.get_important_events("session-1", query="docker build")
    assert calls == [("session-1", 100), ("session-1", 100)]
