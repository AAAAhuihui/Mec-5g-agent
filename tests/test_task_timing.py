from __future__ import annotations

from app.agent import task_timing


def test_task_timing_excludes_approval_wait(monkeypatch) -> None:
    clock = iter([1_000, 1_200, 6_200, 6_500])
    monkeypatch.setattr(task_timing, "_now_ms", lambda: next(clock))
    state = {"task_status": "running"}

    task_timing.start_task_timing(state)
    task_timing.pause_task_timing(state)
    task_timing.resume_task_timing(state)
    state["task_status"] = "completed"
    task_timing.finalize_task_timing(state)

    assert task_timing.task_timing_payload(state) == {
        "total_elapsed_ms": 5_500,
        "active_elapsed_ms": 500,
    }


def test_legacy_task_gets_timing_when_resumed(monkeypatch) -> None:
    monkeypatch.setattr(task_timing, "_now_ms", lambda: 100)
    state = {"task_status": "running"}

    task_timing.ensure_task_timing(state)

    assert state["task_timing"]["started_at_ms"] == 100
    assert task_timing.task_timing_payload(state) == {
        "total_elapsed_ms": None,
        "active_elapsed_ms": None,
    }
