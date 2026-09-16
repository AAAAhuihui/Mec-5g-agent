from __future__ import annotations

import locale
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings


class CmdApprovalError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class PendingCmdCommand:
    approval_id: str
    command: str
    rationale: str
    working_dir: str
    created_at: datetime
    expires_at: datetime
    conversation_id: str | None = None
    status: str = "pending"
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


_APPROVALS: dict[str, PendingCmdCommand] = {}
_APPROVALS_LOCK = threading.RLock()
_MAX_COMMAND_CHARS = 4000
_READ_ONLY_ROOT_COMMANDS = {
    "dir",
    "type",
    "more",
    "find",
    "findstr",
    "where",
    "whoami",
    "ver",
    "systeminfo",
    "tasklist",
    "ipconfig",
}


def request_cmd_approval(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Create an immutable CMD approval. This function never starts cmd.exe."""
    if not settings.cmd_tool_enabled:
        raise CmdApprovalError("CMD 工具未启用；请先将 CMD_TOOL_ENABLED 设为 true。", 403)
    command = str(tool_args.get("command", "")).strip()
    rationale = str(tool_args.get("rationale", "")).strip()
    if not command or not rationale:
        raise CmdApprovalError("CMD 命令和执行说明不能为空。")
    if "\x00" in command or len(command) > _MAX_COMMAND_CHARS:
        raise CmdApprovalError(f"CMD 命令必须为 1 到 {_MAX_COMMAND_CHARS} 个字符，且不能包含 NUL 字符。")
    _validate_cmd_syntax(command)

    now = datetime.now(timezone.utc)
    record = PendingCmdCommand(
        approval_id=uuid4().hex,
        command=command,
        rationale=rationale,
        working_dir=str(_resolve_workdir(tool_args.get("working_dir"))),
        created_at=now,
        expires_at=now + timedelta(seconds=max(1, settings.cmd_tool_approval_ttl_seconds)),
    )
    with _APPROVALS_LOCK:
        _APPROVALS[record.approval_id] = record
    return _public_record(record)


def is_read_only_cmd(command: str) -> bool:
    """Conservative allowlist; uncertain syntax must go through human approval."""
    normalized = command.strip()
    if not normalized or re.search(r"[&|<>`]|\b(del|erase|rd|rmdir|mkdir|copy|move|ren|set|start|shutdown)\b", normalized, re.I):
        return False
    first = normalized.split(maxsplit=1)[0].lower()
    if first in _READ_ONLY_ROOT_COMMANDS:
        return True
    lowered = normalized.lower()
    return bool(
        re.match(r"^sc\s+(query|qc)\b", lowered)
        or re.match(r"^kubectl\s+(get|describe|logs|top)\b", lowered)
    )


def run_read_only_cmd(tool_args: dict[str, Any]) -> dict[str, Any]:
    command = str(tool_args.get("command", "")).strip()
    if not is_read_only_cmd(command):
        return {"success": False, "error": "该 CMD 命令不在只读白名单中。"}
    working_dir = _resolve_workdir(tool_args.get("working_dir"))
    result = _execute_cmd(command, working_dir)
    return {
        "command": command,
        "cwd": str(working_dir),
        "success": result["status"] == "completed" and result.get("exit_code") == 0,
        "status": result["status"],
        "exit_code": result.get("exit_code"),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "error": result.get("error"),
        "timed_out": result.get("status") == "timed_out",
    }


def bind_cmd_approval(approval_id: str, conversation_id: str) -> dict[str, Any]:
    record = _get_record(approval_id)
    with record._lock:
        if record.conversation_id and record.conversation_id != conversation_id:
            raise CmdApprovalError("该 CMD 审批不属于当前会话。", 403)
        if record.status != "pending":
            raise CmdApprovalError("该 CMD 审批已经不能再绑定会话。", 409)
        record.conversation_id = conversation_id
        return _public_record(record)


def approve_cmd_command(approval_id: str, conversation_id: str, approved: bool) -> dict[str, Any]:
    record = _get_record(approval_id)
    with record._lock:
        _assert_owned_and_pending(record, conversation_id)
        if not approved:
            record.status = "rejected"
            return _public_record(record)
        record.status = "running"

    result = _execute_cmd(record.command, Path(record.working_dir))
    with record._lock:
        record.status = result["status"]
        record.exit_code = result["exit_code"]
        record.stdout = result["stdout"]
        record.stderr = result["stderr"]
        record.error = result["error"]
        return _public_record(record)


def _get_record(approval_id: str) -> PendingCmdCommand:
    with _APPROVALS_LOCK:
        record = _APPROVALS.get(approval_id)
    if record is None:
        record = _restore_pending_record(approval_id)
    if record is None:
        raise CmdApprovalError("未找到 CMD 审批记录；它可能已在服务重启后失效。", 404)
    return record


def _restore_pending_record(approval_id: str) -> PendingCmdCommand | None:
    """Rehydrate a pending approval from the persisted ReAct task after restart."""
    try:
        from app.agent.mysql_memory_store import get_react_task_by_approval

        task = get_react_task_by_approval(approval_id)
    except Exception:
        return None
    approval = (task or {}).get("state", {}).get("approval") or {}
    if approval.get("approval_id") != approval_id or approval.get("status") != "pending":
        return None
    try:
        record = PendingCmdCommand(
            approval_id=approval_id,
            command=str(approval["command"]),
            rationale=str(approval.get("rationale", "")),
            working_dir=str(approval["working_dir"]),
            created_at=datetime.fromisoformat(str(approval["created_at"])),
            expires_at=datetime.fromisoformat(str(approval["expires_at"])),
            conversation_id=str((task or {}).get("session_id") or "") or None,
        )
    except (KeyError, TypeError, ValueError):
        return None
    with _APPROVALS_LOCK:
        _APPROVALS[approval_id] = record
    return record


def _assert_owned_and_pending(record: PendingCmdCommand, conversation_id: str) -> None:
    if record.conversation_id != conversation_id:
        raise CmdApprovalError("该 CMD 审批不属于当前会话。", 403)
    if datetime.now(timezone.utc) > record.expires_at:
        if record.status == "pending":
            record.status = "expired"
        raise CmdApprovalError("该 CMD 审批已过期，请重新发起命令。", 410)
    if record.status != "pending":
        raise CmdApprovalError(f"该 CMD 审批当前状态为 {record.status}，不能重复执行。", 409)


def _resolve_workdir(value: Any) -> Path:
    root = settings.cmd_tool_allowed_workdir.resolve()
    candidate = Path(str(value).strip()).expanduser() if value else root
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CmdApprovalError("working_dir 必须位于 CMD_TOOL_ALLOWED_WORKDIR 之内。") from exc
    if not candidate.is_dir():
        raise CmdApprovalError(f"working_dir 不存在或不是目录：{candidate}")
    return candidate


def _validate_cmd_syntax(command: str) -> None:
    lowered = command.lower()
    if "~/" in command or "~\\" in command or "mkdir -p" in lowered:
        raise CmdApprovalError(
            "CMD 工具仅接受 CMD 语法；例如创建桌面目录请使用 "
            'mkdir "%USERPROFILE%\\Desktop\\qdh"。'
        )


def _execute_cmd_raw(command: str, working_dir: Path) -> dict[str, Any]:
    cmd_path = shutil.which("cmd.exe") or shutil.which("cmd")
    if not cmd_path:
        candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
        cmd_path = str(candidate) if candidate.is_file() else None
    if not cmd_path:
        return _result("failed", error="未找到本机 cmd.exe。")
    try:
        completed = subprocess.run(
            # On Windows, passing a quoted CMD command as one list argument makes
            # subprocess escape its quotes again. shell=True lets cmd.exe receive
            # the exact, already-approved command text.
            command,
            shell=True,
            executable=cmd_path,
            cwd=str(working_dir),
            env=_minimal_environment(),
            capture_output=True,
            text=True,
            encoding=locale.getpreferredencoding(False),
            errors="replace",
            timeout=max(1, settings.cmd_tool_timeout_seconds),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _result(
            "timed_out",
            stdout=_truncate(_as_text(exc.stdout)),
            stderr=_truncate(_as_text(exc.stderr)),
            error=f"命令超过 {settings.cmd_tool_timeout_seconds} 秒超时限制。",
        )
    except OSError as exc:
        return _result("failed", error=f"无法启动 cmd.exe：{exc}")
    return _result(
        "completed",
        exit_code=completed.returncode,
        stdout=_truncate(completed.stdout),
        stderr=_truncate(completed.stderr),
    )


def _execute_cmd(command: str, working_dir: Path) -> dict[str, Any]:
    """Return one JSON-serializable result shape for all normal and error paths."""
    result = _execute_cmd_raw(command, working_dir)
    result.setdefault("command", command)
    result.setdefault("cwd", str(working_dir))
    result.setdefault("success", result.get("status") == "completed" and result.get("exit_code") == 0)
    result.setdefault("timed_out", result.get("status") == "timed_out")
    return result


def _minimal_environment() -> dict[str, str]:
    # Keep the execution environment deliberately small, but preserve the values
    # required by pip/requests on Windows to locate user configuration, corporate
    # proxies, and custom CA bundles. These values are never logged or returned to
    # the model.
    keys = (
        "PATH", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
        "SystemRoot", "WINDIR", "TEMP", "TMP", "LANG",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
        "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "PIP_CERT", "PIP_CONFIG_FILE",
        "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST",
        "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
    )
    return {key: os.environ[key] for key in keys if os.environ.get(key)}


def _result(status: str, *, exit_code: int | None = None, stdout: str = "", stderr: str = "", error: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "success": status == "completed" and exit_code == 0,
        "timed_out": status == "timed_out",
        "error": error,
    }


def _public_record(record: PendingCmdCommand) -> dict[str, Any]:
    return {
        "approval_id": record.approval_id,
        "approval_type": "cmd_execute",
        "status": record.status,
        "command": record.command,
        "cwd": record.working_dir,
        "rationale": record.rationale,
        "working_dir": record.working_dir,
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
        "exit_code": record.exit_code,
        "stdout": record.stdout,
        "stderr": record.stderr,
        "success": record.status == "completed" and record.exit_code == 0,
        "timed_out": record.status == "timed_out",
        "error": record.error,
    }


def _truncate(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    value = value or ""
    maximum = max(1, settings.cmd_tool_max_output_chars)
    if len(value) <= maximum:
        return value
    marker = f"\n... [output truncated to {maximum} chars; head and tail retained] ...\n"
    remaining = max(2, maximum - len(marker))
    head = remaining // 2
    tail = remaining - head
    return value[:head] + marker + value[-tail:]
    return value if len(value) <= maximum else value[:maximum] + f"\n... [输出已截断，最大 {maximum} 字符]"


def _as_text(value: str | bytes | None) -> str:
    return _truncate(value)
