"""Repository boundary for structured summaries and durable memory events."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from app.agent import mysql_memory_store
from app.agent.redis_cache_repository import RedisCacheRepository


class SummaryRepository:
    def __init__(self, cache: RedisCacheRepository | None = None) -> None:
        self._cache = cache or RedisCacheRepository()

    def get_semantic_summary(self, session_id: str) -> str:
        key = self._cache.summary_key(session_id, "semantic")
        cached = self._cache.get_json(key)
        if isinstance(cached, str):
            return cached
        session = mysql_memory_store.get_session(session_id) or {}
        summary = str(session.get("semantic_summary") or session.get("summary") or "")
        self._cache.set_json(key, summary)
        return summary

    def get_task_summary(self, session_id: str) -> str:
        key = self._cache.summary_key(session_id, "task")
        cached = self._cache.get_json(key)
        if isinstance(cached, str):
            return cached
        session = mysql_memory_store.get_session(session_id) or {}
        summary = str(session.get("task_summary") or "{}")
        self._cache.set_json(key, summary)
        return summary

    def update_semantic_summary(self, session_id: str, summary: str) -> None:
        mysql_memory_store.update_semantic_summary(session_id, summary)
        self._cache.set_json(self._cache.summary_key(session_id, "semantic"), summary)

    def update_task_summary(self, session_id: str, summary: str) -> None:
        mysql_memory_store.update_task_summary(session_id, summary)
        self._cache.set_json(self._cache.summary_key(session_id, "task"), summary)

    def invalidate(self, session_id: str) -> None:
        self._cache.delete(self._cache.summary_key(session_id, "semantic"))
        self._cache.delete(self._cache.summary_key(session_id, "task"))


class EventRepository:
    def __init__(self, cache: RedisCacheRepository | None = None) -> None:
        self._cache = cache or RedisCacheRepository()

    def create_event(
        self,
        session_id: str,
        *,
        event_type: str,
        content: str,
        importance: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        mysql_memory_store.create_event(
            session_id,
            event_type=event_type,
            content=content,
            importance=importance,
            metadata=metadata,
        )
        self._cache.bump_event_version(session_id)

    def get_recent_events(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return mysql_memory_store.list_recent_events(session_id, limit=limit)

    def get_important_events(
        self,
        session_id: str,
        *,
        query: str = "",
        limit: int = 10,
        min_importance: float = 0.7,
    ) -> list[dict[str, Any]]:
        cache_key = self._cache.important_events_key(session_id, query, limit, min_importance)
        cached = self._cache.get_json(cache_key)
        if isinstance(cached, list) and all(isinstance(item, dict) for item in cached):
            return cached
        candidates = [
            event for event in self.get_recent_events(session_id, limit=100)
            if float(event.get("importance", 0.0)) >= min_importance
        ]
        now = datetime.now(timezone.utc)
        query_terms = _query_terms(query)
        for event in candidates:
            text = f"{event.get('event_type', '')} {event.get('content', '')}".casefold()
            relevance = 1.0 if not query_terms else sum(term in text for term in query_terms) / len(query_terms)
            created = event.get("created_time")
            if getattr(created, "tzinfo", None) is None:
                created = created.replace(tzinfo=timezone.utc) if isinstance(created, datetime) else now
            age_days = max(0.0, (now - created).total_seconds() / 86400)
            recency = max(0.0, 1.0 - min(age_days / 30.0, 1.0))
            event["memory_score"] = round(
                0.5 * float(event.get("importance", 0.0)) + 0.3 * relevance + 0.2 * recency,
                4,
            )
        ranked = sorted(candidates, key=lambda item: item["memory_score"], reverse=True)[:limit]
        for event in ranked:
            created = event.get("created_time")
            if isinstance(created, datetime):
                event["created_time"] = created.isoformat()
        self._cache.set_json(cache_key, ranked)
        return ranked

    def delete_old_events(self, session_id: str, keep: int = 200) -> int:
        deleted = mysql_memory_store.delete_old_events(session_id, keep=keep)
        if deleted:
            self._cache.bump_event_version(session_id)
        return deleted

    def invalidate(self, session_id: str) -> None:
        self._cache.bump_event_version(session_id)


def _query_terms(query: str) -> set[str]:
    """Produce useful terms for both whitespace-delimited and Chinese queries."""
    words = {item.casefold() for item in re.findall(r"[A-Za-z0-9_./-]{2,}", query)}
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", query))
    # Bigrams avoid treating every single Chinese character as a high-signal
    # match while still providing a relevance signal for unsegmented text.
    words.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return {item for item in words if item}
