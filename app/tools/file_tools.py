"""Bounded local file read/write tools with explicit approval for writes."""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings


class FileToolError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class PendingFileWrite:
    approval_id: str
    path: str
    content: str
    rationale: str
    overwrite: bool
    created_at: datetime
    expires_at: datetime
    conversation_id: str | None = None
    status: str = "pending"
    error: str | None = None
    approval_type: str = "file_write"
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


_APPROVALS: dict[str, PendingFileWrite] = {}
_LOCK = threading.RLock()
_TEXT_SUFFIXES = {".py", ".html", ".htm", ".js", ".css", ".json", ".md", ".txt", ".yaml", ".yml"}


def find_files(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Find a bounded set of text files under the allowed root without reading them."""
    query = str(tool_args.get("query", "")).strip().casefold()
    if not query:
        return {"success": False, "error": "必须提供文件名或关键词。"}
    root = settings.file_tool_allowed_root.expanduser().resolve()
    if not root.is_dir():
        return {"success": False, "error": f"允许目录不存在：{root}"}
    matches: list[dict[str, Any]] = []
    try:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in _TEXT_SUFFIXES:
                continue
            if query not in path.name.casefold() and query not in str(path.relative_to(root)).casefold():
                continue
            matches.append({"path": str(path), "size": path.stat().st_size})
            if len(matches) >= 30:
                break
    except OSError as exc:
        return {"success": False, "error": f"搜索文件失败：{exc}"}
    return {"success": True, "query": query, "root": str(root), "files": matches, "count": len(matches)}


def read_file(tool_args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path(tool_args.get("path"), must_exist=True)
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"success": False, "path": str(path), "error": f"无法读取文件：{exc}"}
    maximum = max(1, settings.file_tool_max_read_chars)
    truncated = len(content) > maximum
    if truncated:
        content = content[:maximum] + "\n... [文件内容已截断]"
    return {
        "success": True,
        "path": str(path),
        "content": content,
        "chars": len(content),
        "truncated": truncated,
        "sha256": _hash_text(content),
    }


def request_file_write_approval(tool_args: dict[str, Any]) -> dict[str, Any]:
    if not settings.file_tool_enabled:
        raise FileToolError("文件写入工具未启用；请将 FILE_TOOL_ENABLED 设为 true。", 403)
    path = _resolve_path(tool_args.get("path"), must_exist=False)
    content = str(tool_args.get("content", ""))
    rationale = str(tool_args.get("rationale", "")).strip()
    overwrite = bool(tool_args.get("overwrite", False))
    if not content:
        raise FileToolError("文件内容不能为空。")
    if not rationale:
        raise FileToolError("必须说明文件写入用途。")
    if len(content) > settings.file_tool_max_write_chars:
        raise FileToolError(f"文件内容超过 {settings.file_tool_max_write_chars} 字符上限。")
    now = datetime.now(timezone.utc)
    record = PendingFileWrite(
        approval_id=uuid4().hex,
        path=str(path),
        content=content,
        rationale=rationale,
        overwrite=overwrite,
        created_at=now,
        expires_at=now + timedelta(seconds=max(1, settings.cmd_tool_approval_ttl_seconds)),
    )
    with _LOCK:
        _APPROVALS[record.approval_id] = record
    return _public(record)


def request_file_delete_approval(tool_args: dict[str, Any]) -> dict[str, Any]:
    if not settings.file_tool_enabled:
        raise FileToolError("文件删除工具未启用；请将 FILE_TOOL_ENABLED 设为 true。", 403)
    path = _resolve_path(tool_args.get("path"), must_exist=True)
    rationale = str(tool_args.get("rationale", "")).strip()
    if not rationale:
        raise FileToolError("必须说明文件删除用途。")
    now = datetime.now(timezone.utc)
    record = PendingFileWrite(
        approval_id=uuid4().hex,
        path=str(path), content="", rationale=rationale, overwrite=False,
        created_at=now,
        expires_at=now + timedelta(seconds=max(1, settings.cmd_tool_approval_ttl_seconds)),
        approval_type="file_delete",
    )
    with _LOCK:
        _APPROVALS[record.approval_id] = record
    return _public(record)


def bind_file_approval(approval_id: str, conversation_id: str) -> dict[str, Any]:
    record = _get_record(approval_id)
    with record._lock:
        if record.conversation_id and record.conversation_id != conversation_id:
            raise FileToolError("该文件写入审批不属于当前会话。", 403)
        if record.status != "pending":
            raise FileToolError("该文件写入审批已不可再绑定。", 409)
        record.conversation_id = conversation_id
        return _public(record)


def approve_file_operation(approval_id: str, conversation_id: str, approved: bool) -> dict[str, Any]:
    record = _get_record(approval_id)
    with record._lock:
        if record.conversation_id != conversation_id:
            raise FileToolError("该文件写入审批不属于当前会话。", 403)
        if datetime.now(timezone.utc) > record.expires_at:
            record.status = "expired"
            raise FileToolError("该文件写入审批已过期。", 410)
        if record.status != "pending":
            raise FileToolError(f"该文件写入审批当前状态为 {record.status}，不能重复执行。", 409)
        if not approved:
            record.status = "rejected"
            return _public(record)
        record.status = "running"

    path = Path(record.path)
    try:
        if record.approval_type == "file_delete":
            path.unlink()
        elif path.exists() and not record.overwrite:
            raise FileExistsError("目标文件已存在；请明确将 overwrite 设为 true。")
        elif record.approval_type == "file_write":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(record.content, encoding="utf-8")
        else:
            raise FileToolError(f"不支持的文件审批类型：{record.approval_type}")
        status, error = "completed", None
    except OSError as exc:
        status, error = "failed", str(exc)
    with record._lock:
        record.status = status
        record.error = error
        return _public(record)


def approve_file_write(approval_id: str, conversation_id: str, approved: bool) -> dict[str, Any]:
    """Compatibility alias for existing callers."""
    return approve_file_operation(approval_id, conversation_id, approved)


def _get_record(approval_id: str) -> PendingFileWrite:
    with _LOCK:
        record = _APPROVALS.get(approval_id)
    if record is None:
        record = _restore_record(approval_id)
    if record is None:
        raise FileToolError("未找到文件写入审批记录。", 404)
    return record


def _restore_record(approval_id: str) -> PendingFileWrite | None:
    try:
        from app.agent.mysql_memory_store import get_react_task_by_approval

        task = get_react_task_by_approval(approval_id)
        approval = (task or {}).get("state", {}).get("approval") or {}
        if approval.get("approval_id") != approval_id or approval.get("approval_type") not in {"file_write", "file_delete"}:
            return None
        record = PendingFileWrite(
            approval_id=approval_id,
            path=str(approval["path"]), content=str(approval.get("content", "")),
            rationale=str(approval["rationale"]), overwrite=bool(approval.get("overwrite", False)),
            created_at=datetime.fromisoformat(str(approval["created_at"])),
            expires_at=datetime.fromisoformat(str(approval["expires_at"])),
            conversation_id=str((task or {}).get("session_id") or "") or None,
            approval_type=str(approval.get("approval_type", "file_write")),
        )
    except (KeyError, TypeError, ValueError, OSError):
        return None
    with _LOCK:
        _APPROVALS[approval_id] = record
    return record


def _resolve_path(value: Any, *, must_exist: bool) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise FileToolError("必须提供文件路径。")
    root = settings.file_tool_allowed_root.expanduser().resolve()
    candidate = Path(os.path.expandvars(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FileToolError(f"文件路径必须位于允许目录内：{root}") from exc
    if must_exist and (not candidate.is_file()):
        raise FileToolError(f"文件不存在或不是普通文件：{candidate}", 404)
    return candidate


def _public(record: PendingFileWrite) -> dict[str, Any]:
    return {
        "approval_id": record.approval_id,
        "approval_type": record.approval_type,
        "status": record.status,
        "path": record.path,
        "rationale": record.rationale,
        "overwrite": record.overwrite,
        "content": record.content,
        "content_preview": record.content[:1000],
        "content_sha256": _hash_text(record.content),
        "content_chars": len(record.content),
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
        "success": record.status == "completed",
        "error": record.error,
    }


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
