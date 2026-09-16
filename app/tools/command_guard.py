"""Command normalization, loop detection, and progress evaluation for ReAct CMD calls."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from typing import Any


def normalize_command(command: str) -> str:
    """Create a conservative comparison form without changing the command to execute.

    Windows CMD quoting does not exactly match POSIX shell quoting, so parsing is
    best-effort only. The original command is always kept and executed unchanged.
    """
    text = str(command or "").strip()
    if not text:
        return ""
    try:
        tokens = shlex.split(text, posix=False)
        normalized = " ".join(tokens)
    except ValueError:
        normalized = re.sub(r"\s+", " ", text)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def command_hash(normalized_command: str) -> str:
    return _hash_text(normalized_command)


def result_hash(result: dict[str, Any]) -> str:
    """Hash only the outcome fields that describe observable command state."""
    payload = {
        "exit_code": result.get("exit_code"),
        "stdout": str(result.get("stdout", "")),
        "stderr": str(result.get("stderr", "")),
        "status": str(result.get("status", "")),
        "timed_out": bool(result.get("timed_out", False)),
    }
    return _hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def build_command_record(command: str, cwd: str, result: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_command(command)
    status = str(result.get("status", ""))
    exit_code = result.get("exit_code")
    success = bool(result.get("success", status == "completed" and exit_code == 0))
    return {
        "command": command,
        "normalized_command": normalized,
        "cwd": cwd,
        "exit_code": exit_code,
        "stdout": str(result.get("stdout", "")),
        "stderr": str(result.get("stderr", "")),
        "success": success,
        "status": status,
        "timed_out": bool(result.get("timed_out", status == "timed_out")),
        "error": result.get("error"),
        "command_hash": command_hash(normalized),
        "result_hash": result_hash(result),
    }


def detect_command_loop(
    command_history: list[dict[str, Any]],
    next_command: str,
    repeat_limit: int = 2,
) -> bool:
    """Return true only after the same command produced the same result repeatedly."""
    normalized = normalize_command(next_command)
    if not normalized or repeat_limit < 1:
        return False
    matches = [
        item
        for item in command_history
        if item.get("normalized_command") == normalized and item.get("result_hash")
    ]
    if len(matches) < repeat_limit:
        return False
    recent = matches[-repeat_limit:]
    return len({str(item["result_hash"]) for item in recent}) == 1


def evaluate_command_progress(
    command_history: list[dict[str, Any]],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Treat a changed result as new information; identical results as no progress."""
    previous = [
        item
        for item in command_history[:-1]
        if item.get("normalized_command") == current.get("normalized_command")
    ]
    if not previous:
        return {
            "has_progress": True,
            "task_completed": False,
            "reason": "首次执行该命令，获得了新的观察结果。",
            "recommended_next_action": "根据结果选择下一项计划步骤或汇总回答。",
        }
    if previous[-1].get("result_hash") != current.get("result_hash"):
        return {
            "has_progress": True,
            "task_completed": False,
            "reason": "相同命令返回了不同结果，环境状态可能发生变化。",
            "recommended_next_action": "使用最新结果继续诊断。",
        }
    return {
        "has_progress": False,
        "task_completed": False,
        "reason": "相同命令再次返回相同结果，没有获得新信息。",
        "recommended_next_action": "不要重试该命令；选择实质不同的诊断步骤、请求缺失信息或结束任务。",
    }


def redact_command(command: str) -> str:
    """Keep useful audit context while avoiding common inline credential leaks."""
    value = str(command)
    value = re.sub(r"(?i)(password|passwd|token|api[_-]?key)\s*=\s*[^\s&|]+", r"\1=<redacted>", value)
    value = re.sub(r"(?i)(-p)([^\s]+)", r"\1<redacted>", value)
    return value


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
