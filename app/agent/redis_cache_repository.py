"""Best-effort Redis cache for Memory reads.

Redis is intentionally never treated as durable state: every cache error is a
cache miss and callers continue with MySQL.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config import settings


class RedisCacheRepository:
    def __init__(self, client: Any | None = None) -> None:
        self._client = client if client is not None else _create_client()

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def get_json(self, key: str) -> Any | None:
        if self._client is None:
            return None
        try:
            raw = self._client.get(key)
            if raw is None:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except Exception:
            return None

    def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        if self._client is None:
            return
        try:
            self._client.set(
                key,
                json.dumps(value, ensure_ascii=False, default=str),
                ex=max(1, ttl_seconds or settings.redis_context_ttl_seconds),
            )
        except Exception:
            return

    def delete(self, key: str) -> None:
        if self._client is None:
            return
        try:
            self._client.delete(key)
        except Exception:
            return

    def get_event_version(self, session_id: str) -> int:
        value = self.get_json(_event_version_key(session_id))
        return int(value) if isinstance(value, int) else 0

    def bump_event_version(self, session_id: str) -> None:
        if self._client is None:
            return
        key = _event_version_key(session_id)
        try:
            self._client.incr(key)
            self._client.expire(key, max(1, settings.redis_context_ttl_seconds))
        except Exception:
            return

    @staticmethod
    def summary_key(session_id: str, summary_type: str) -> str:
        return f"agent:memory:summary:{_safe_part(session_id)}:{summary_type}:v1"

    def important_events_key(self, session_id: str, query: str, limit: int, min_importance: float) -> str:
        version = self.get_event_version(session_id)
        fingerprint = hashlib.sha256(
            f"{query.casefold().strip()}|{limit}|{min_importance}".encode("utf-8")
        ).hexdigest()[:24]
        return f"agent:memory:events:{_safe_part(session_id)}:v{version}:{fingerprint}"


def _create_client() -> Any | None:
    if not settings.redis_host:
        return None
    try:
        import redis

        # Do not ping during construction. A network failure is handled as a
        # cache miss for the individual operation instead of delaying chat.
        return redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
            decode_responses=True,
        )
    except Exception:
        return None


def _event_version_key(session_id: str) -> str:
    return f"agent:memory:events-version:{_safe_part(session_id)}"


def _safe_part(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]
