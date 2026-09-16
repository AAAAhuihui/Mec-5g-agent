"""Durable wall-clock timing for one ReAct task."""

from __future__ import annotations

import time
from typing import Any


FINAL_TASK_STATUSES = {"completed", "blocked", "failed", "rejected"}


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def start_task_timing(state: dict[str, Any]) -> None:
    state["task_timing"] = {
        "started_at_ms": _now_ms(),
        "paused_at_ms": None,
        "paused_elapsed_ms": 0,
        "total_elapsed_ms": None,
        "active_elapsed_ms": None,
    }


def ensure_task_timing(state: dict[str, Any]) -> None:
    """Keep persisted tasks created before timing support resumable."""
    timing = state.get("task_timing")
    if not isinstance(timing, dict) or not isinstance(timing.get("started_at_ms"), int):
        start_task_timing(state)
        return
    timing.setdefault("paused_at_ms", None)
    timing.setdefault("paused_elapsed_ms", 0)
    timing.setdefault("total_elapsed_ms", None)
    timing.setdefault("active_elapsed_ms", None)


def pause_task_timing(state: dict[str, Any]) -> None:
    ensure_task_timing(state)
    timing = state["task_timing"]
    if timing.get("paused_at_ms") is None:
        timing["paused_at_ms"] = _now_ms()


def resume_task_timing(state: dict[str, Any]) -> None:
    ensure_task_timing(state)
    timing = state["task_timing"]
    paused_at = timing.get("paused_at_ms")
    if isinstance(paused_at, int):
        timing["paused_elapsed_ms"] = max(
            0,
            int(timing.get("paused_elapsed_ms", 0)) + _now_ms() - paused_at,
        )
        timing["paused_at_ms"] = None


def finalize_task_timing(state: dict[str, Any]) -> None:
    """Freeze total and active duration once a task reaches a terminal status."""
    ensure_task_timing(state)
    if state.get("task_status") not in FINAL_TASK_STATUSES:
        return
    resume_task_timing(state)
    timing = state["task_timing"]
    total = max(0, _now_ms() - int(timing["started_at_ms"]))
    paused = max(0, int(timing.get("paused_elapsed_ms", 0)))
    timing["total_elapsed_ms"] = total
    timing["active_elapsed_ms"] = max(0, total - paused)


def task_timing_payload(state: dict[str, Any]) -> dict[str, int | None]:
    """Public API payload: expose only the two requested elapsed values."""
    timing = state.get("task_timing") if isinstance(state.get("task_timing"), dict) else {}
    return {
        "total_elapsed_ms": _as_optional_int(timing.get("total_elapsed_ms")),
        "active_elapsed_ms": _as_optional_int(timing.get("active_elapsed_ms")),
    }


def _as_optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None
