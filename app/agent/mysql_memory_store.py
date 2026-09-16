from __future__ import annotations

import json
from typing import Any

from app.config import settings


class MySQLMemoryError(RuntimeError):
    pass


def _import_pymysql() -> Any:
    try:
        import pymysql
    except ImportError as exc:
        raise MySQLMemoryError(
            "PyMySQL is not installed. Run: .\\.venv\\Scripts\\python.exe -m pip install pymysql"
        ) from exc
    return pymysql


def _connect(database: str | None = None) -> Any:
    pymysql = _import_pymysql()
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=database,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def ensure_schema() -> None:
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        connection.close()

        connection = _connect(settings.mysql_database)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id VARCHAR(64) PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    summary LONGTEXT NULL,
                    semantic_summary TEXT NULL,
                    task_summary TEXT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            _ensure_chat_sessions_summary_column(cursor)
            _ensure_chat_sessions_memory_columns(cursor)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    session_id VARCHAR(64) NOT NULL,
                    role VARCHAR(32) NOT NULL,
                    content LONGTEXT NOT NULL,
                    intent VARCHAR(64) NULL,
                    evidence_sources TEXT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_chat_messages_session_created (session_id, created_at),
                    CONSTRAINT fk_chat_messages_session
                        FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
                        ON DELETE CASCADE
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_operations (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    session_id VARCHAR(64) NOT NULL,
                    standalone_question LONGTEXT NOT NULL,
                    intent VARCHAR(64) NULL,
                    selected_tool VARCHAR(64) NULL,
                    tool_args LONGTEXT NULL,
                    queries LONGTEXT NULL,
                    evidence_sources TEXT NULL,
                    retrieval_grade VARCHAR(64) NULL,
                    retrieval_score DOUBLE NULL,
                    self_check_result LONGTEXT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_chat_operations_session_created (session_id, created_at),
                    CONSTRAINT fk_chat_operations_session
                        FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
                        ON DELETE CASCADE
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS react_tasks (
                    task_id VARCHAR(64) PRIMARY KEY,
                    session_id VARCHAR(64) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    approval_id VARCHAR(64) NULL,
                    state_json LONGTEXT NOT NULL,
                    total_elapsed_ms BIGINT NULL,
                    active_elapsed_ms BIGINT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_react_tasks_session_updated (session_id, updated_at),
                    INDEX idx_react_tasks_approval (approval_id),
                    CONSTRAINT fk_react_tasks_session
                        FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
                        ON DELETE CASCADE
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            _ensure_react_task_timing_columns(cursor)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS session_artifacts (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    session_id VARCHAR(64) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    artifact_type VARCHAR(50) NOT NULL DEFAULT 'file',
                    file_path VARCHAR(1024) NOT NULL,
                    path_hash CHAR(64) NOT NULL,
                    summary TEXT NULL,
                    content_sha256 CHAR(64) NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_session_artifacts_path (session_id, path_hash),
                    INDEX idx_session_artifacts_session_updated (session_id, updated_at),
                    CONSTRAINT fk_session_artifacts_session
                        FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
                        ON DELETE CASCADE
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_events (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    session_id VARCHAR(64) NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    content TEXT NOT NULL,
                    importance FLOAT NOT NULL,
                    metadata JSON NULL,
                    created_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_chat_events_session_created (session_id, created_time),
                    INDEX idx_chat_events_session_importance (session_id, importance),
                    CONSTRAINT fk_chat_events_session
                        FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
                        ON DELETE CASCADE
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
        connection.close()
    except Exception as exc:
        if isinstance(exc, MySQLMemoryError):
            raise
        raise MySQLMemoryError(f"MySQL memory initialization failed: {exc}") from exc


def create_session(session_id: str, title: str) -> None:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO chat_sessions (id, title)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, title),
        )
    connection.close()


def session_exists(session_id: str) -> bool:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM chat_sessions WHERE id = %s", (session_id,))
        row = cursor.fetchone()
    connection.close()
    return row is not None


def get_session(session_id: str) -> dict[str, Any] | None:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, title, summary, semantic_summary, task_summary, created_at, updated_at FROM chat_sessions WHERE id = %s",
            (session_id,),
        )
        row = cursor.fetchone()
    connection.close()
    return row


def add_message(
    session_id: str,
    role: str,
    content: str,
    intent: str | None = None,
    evidence_sources: list[str] | None = None,
) -> None:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO chat_messages (session_id, role, content, intent, evidence_sources)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                session_id,
                role,
                content,
                intent,
                json.dumps(evidence_sources or [], ensure_ascii=False),
            ),
        )
        title = _title_from_content(content)
        cursor.execute(
            """
            UPDATE chat_sessions
            SET title = CASE WHEN title = '新会话' THEN %s ELSE title END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (title, session_id),
        )
    connection.close()


def list_sessions(limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                s.id,
                s.title,
                s.created_at,
                s.updated_at,
                COUNT(m.id) AS message_count
            FROM chat_sessions s
            LEFT JOIN chat_messages m ON m.session_id = s.id
            GROUP BY s.id, s.title, s.created_at, s.updated_at
            ORDER BY s.updated_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()
    connection.close()
    return rows


def list_messages(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, session_id, role, content, intent, evidence_sources, created_at
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (session_id, limit),
        )
        rows = list(reversed(cursor.fetchall()))
    connection.close()
    for row in rows:
        row["evidence_sources"] = _parse_json(row.get("evidence_sources"))
    return rows


def add_operation(
    session_id: str,
    standalone_question: str,
    intent: str | None = None,
    selected_tool: str | None = None,
    tool_args: dict[str, Any] | None = None,
    queries: list[str] | None = None,
    evidence_sources: list[str] | None = None,
    retrieval_grade: str | None = None,
    retrieval_score: float | None = None,
    self_check_result: dict[str, Any] | None = None,
) -> None:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO chat_operations (
                session_id,
                standalone_question,
                intent,
                selected_tool,
                tool_args,
                queries,
                evidence_sources,
                retrieval_grade,
                retrieval_score,
                self_check_result
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                standalone_question,
                intent,
                selected_tool,
                json.dumps(tool_args or {}, ensure_ascii=False),
                json.dumps(queries or [], ensure_ascii=False),
                json.dumps(evidence_sources or [], ensure_ascii=False),
                retrieval_grade,
                retrieval_score,
                json.dumps(self_check_result or {}, ensure_ascii=False),
            ),
        )
    connection.close()


def list_operations(session_id: str, limit: int = 3) -> list[dict[str, Any]]:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                id,
                session_id,
                standalone_question,
                intent,
                selected_tool,
                tool_args,
                queries,
                evidence_sources,
                retrieval_grade,
                retrieval_score,
                self_check_result,
                created_at
            FROM chat_operations
            WHERE session_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (session_id, limit),
        )
        rows = list(reversed(cursor.fetchall()))
    connection.close()
    for row in rows:
        row["tool_args"] = _parse_json(row.get("tool_args"))
        row["queries"] = _parse_json(row.get("queries"))
        row["evidence_sources"] = _parse_json(row.get("evidence_sources"))
        row["self_check_result"] = _parse_json(row.get("self_check_result"))
    return rows


def reset_session(session_id: str) -> None:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM chat_messages WHERE session_id = %s", (session_id,))
        cursor.execute("DELETE FROM chat_operations WHERE session_id = %s", (session_id,))
        cursor.execute("DELETE FROM chat_events WHERE session_id = %s", (session_id,))
        cursor.execute("DELETE FROM session_artifacts WHERE session_id = %s", (session_id,))
        cursor.execute(
            """
            UPDATE chat_sessions
            SET title = '新会话',
                summary = NULL,
                semantic_summary = NULL,
                task_summary = '{}',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (session_id,),
        )
    connection.close()


def delete_session(session_id: str) -> bool:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))
        deleted = cursor.rowcount > 0
    connection.close()
    return deleted


def update_session_summary(session_id: str, summary: str) -> None:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE chat_sessions
            SET summary = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (summary, session_id),
        )
    connection.close()


def update_semantic_summary(session_id: str, summary: str) -> None:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE chat_sessions SET semantic_summary = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (summary, session_id),
        )
    connection.close()


def update_task_summary(session_id: str, summary: str) -> None:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE chat_sessions SET task_summary = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (summary, session_id),
        )
    connection.close()


def get_latest_react_task(session_id: str) -> dict[str, Any] | None:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT task_id, status, approval_id, state_json, total_elapsed_ms, active_elapsed_ms, created_at, updated_at
            FROM react_tasks WHERE session_id = %s
            ORDER BY updated_at DESC LIMIT 1
            """,
            (session_id,),
        )
        row = cursor.fetchone()
    connection.close()
    if row:
        row["state"] = _parse_json(row.get("state_json"))
        _hydrate_task_timing(row)
    return row


def save_react_task(session_id: str, state: dict[str, Any]) -> None:
    ensure_schema()
    task_id = str(state["task_id"])
    approval_id = str((state.get("approval") or {}).get("approval_id") or "") or None
    timing = state.get("task_timing") if isinstance(state.get("task_timing"), dict) else {}
    total_elapsed_ms = timing.get("total_elapsed_ms")
    active_elapsed_ms = timing.get("active_elapsed_ms")
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO react_tasks
                (task_id, session_id, status, approval_id, state_json, total_elapsed_ms, active_elapsed_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                approval_id = VALUES(approval_id),
                state_json = VALUES(state_json),
                total_elapsed_ms = VALUES(total_elapsed_ms),
                active_elapsed_ms = VALUES(active_elapsed_ms),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                task_id,
                session_id,
                str(state.get("task_status", "running")),
                approval_id,
                json.dumps(state, ensure_ascii=False, default=str),
                _optional_nonnegative_int(total_elapsed_ms),
                _optional_nonnegative_int(active_elapsed_ms),
            ),
        )
    connection.close()


def get_react_task_by_approval(approval_id: str) -> dict[str, Any] | None:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT task_id, session_id, status, approval_id, state_json, total_elapsed_ms, active_elapsed_ms, created_at, updated_at
            FROM react_tasks
            WHERE approval_id = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (approval_id,),
        )
        row = cursor.fetchone()
    connection.close()
    if not row:
        return None
    row["state"] = _parse_json(row.get("state_json"))
    _hydrate_task_timing(row)
    return row


def upsert_session_artifact(
    session_id: str,
    *,
    name: str,
    artifact_type: str,
    file_path: str,
    summary: str,
    content_sha256: str | None,
) -> None:
    """Save a durable, structured reference to a file produced in this session."""
    import hashlib

    ensure_schema()
    path_hash = hashlib.sha256(file_path.casefold().encode("utf-8")).hexdigest()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO session_artifacts
                (session_id, name, artifact_type, file_path, path_hash, summary, content_sha256)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                name = VALUES(name),
                artifact_type = VALUES(artifact_type),
                summary = VALUES(summary),
                content_sha256 = VALUES(content_sha256),
                updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, name[:255], artifact_type[:50], file_path, path_hash, summary[:4000], content_sha256),
        )
    connection.close()


def list_session_artifacts(session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, session_id, name, artifact_type, file_path, summary, content_sha256, created_at, updated_at
            FROM session_artifacts
            WHERE session_id = %s
            ORDER BY updated_at DESC, id DESC
            LIMIT %s
            """,
            (session_id, limit),
        )
        rows = cursor.fetchall()
    connection.close()
    return rows


def delete_session_artifact_by_path(session_id: str, file_path: str) -> bool:
    import hashlib

    ensure_schema()
    path_hash = hashlib.sha256(file_path.casefold().encode("utf-8")).hexdigest()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM session_artifacts WHERE session_id = %s AND path_hash = %s",
            (session_id, path_hash),
        )
        deleted = cursor.rowcount > 0
    connection.close()
    return deleted


def reconcile_session_artifacts(session_id: str) -> list[str]:
    """Remove stale records only after checking that their files no longer exist."""
    from pathlib import Path

    artifacts = list_session_artifacts(session_id, limit=100)
    removed: list[str] = []
    for artifact in artifacts:
        file_path = str(artifact.get("file_path", ""))
        try:
            exists = Path(file_path).is_file()
        except OSError:
            exists = False
        if not exists and delete_session_artifact_by_path(session_id, file_path):
            removed.append(file_path)
    return removed


def create_event(
    session_id: str,
    *,
    event_type: str,
    content: str,
    importance: float,
    metadata: dict[str, Any] | None = None,
) -> None:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO chat_events (session_id, event_type, content, importance, metadata)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (session_id, event_type, content, max(0.0, min(1.0, importance)), json.dumps(metadata or {}, ensure_ascii=False)),
        )
    connection.close()


def list_recent_events(session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, session_id, event_type, content, importance, metadata, created_time
            FROM chat_events WHERE session_id = %s
            ORDER BY created_time DESC, id DESC LIMIT %s
            """,
            (session_id, limit),
        )
        rows = cursor.fetchall()
    connection.close()
    for row in rows:
        row["metadata"] = _parse_json(row.get("metadata"))
    return rows


def delete_old_events(session_id: str, keep: int = 200) -> int:
    ensure_schema()
    connection = _connect(settings.mysql_database)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM chat_events
            WHERE session_id = %s
              AND id NOT IN (
                SELECT id FROM (
                    SELECT id FROM chat_events WHERE session_id = %s
                    ORDER BY created_time DESC, id DESC LIMIT %s
                ) AS retained_events
              )
            """,
            (session_id, session_id, max(1, keep)),
        )
        deleted = cursor.rowcount
    connection.close()
    return deleted


def _ensure_chat_sessions_summary_column(cursor: Any) -> None:
    cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'chat_sessions'
          AND COLUMN_NAME = 'summary'
        """,
        (settings.mysql_database,),
    )
    row = cursor.fetchone() or {}
    if int(row.get("count", 0)) == 0:
        cursor.execute("ALTER TABLE chat_sessions ADD COLUMN summary LONGTEXT NULL AFTER title")


def _ensure_chat_sessions_memory_columns(cursor: Any) -> None:
    for name in ("semantic_summary", "task_summary"):
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'chat_sessions' AND COLUMN_NAME = %s
            """,
            (settings.mysql_database, name),
        )
        row = cursor.fetchone() or {}
        if int(row.get("count", 0)) == 0:
            cursor.execute(f"ALTER TABLE chat_sessions ADD COLUMN `{name}` TEXT NULL")
    cursor.execute(
        """
        UPDATE chat_sessions
        SET semantic_summary = summary
        WHERE (semantic_summary IS NULL OR semantic_summary = '') AND summary IS NOT NULL AND summary <> ''
        """
    )
    cursor.execute("UPDATE chat_sessions SET task_summary = '{}' WHERE task_summary IS NULL OR task_summary = ''")


def _ensure_react_task_timing_columns(cursor: Any) -> None:
    for name in ("total_elapsed_ms", "active_elapsed_ms"):
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'react_tasks' AND COLUMN_NAME = %s
            """,
            (settings.mysql_database, name),
        )
        row = cursor.fetchone() or {}
        if int(row.get("count", 0)) == 0:
            cursor.execute(f"ALTER TABLE react_tasks ADD COLUMN `{name}` BIGINT NULL")


def _hydrate_task_timing(row: dict[str, Any]) -> None:
    state = row.get("state")
    if not isinstance(state, dict):
        return
    timing = state.get("task_timing")
    if not isinstance(timing, dict):
        timing = {}
        state["task_timing"] = timing
    for key in ("total_elapsed_ms", "active_elapsed_ms"):
        if row.get(key) is not None:
            timing[key] = int(row[key])


def _optional_nonnegative_int(value: Any) -> int | None:
    if not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def _title_from_content(content: str) -> str:
    title = content.strip().replace("\n", " ")
    return title[:60] if title else "新会话"


def _parse_json(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
