from __future__ import annotations

from app.tools.command_guard import (
    build_command_record,
    detect_command_loop,
    evaluate_command_progress,
    normalize_command,
)
from app.agent.react_workflow import ReActWorkflow
from app.agent.state import initial_state
from app.agent.workflow import RAGWorkflow


def _record(command: str, *, stdout: str = "", stderr: str = "", exit_code: int = 0) -> dict:
    return build_command_record(
        command,
        "C:\\work",
        {"status": "completed", "exit_code": exit_code, "stdout": stdout, "stderr": stderr},
    )


def test_normalize_command_ignores_whitespace_and_case() -> None:
    assert normalize_command("kubectl get pods") == normalize_command("  KUBECTL   get   pods  ")


def test_same_command_same_result_is_blocked_on_third_attempt() -> None:
    history = [_record("kubectl get pods", stdout="Pending"), _record("kubectl  get pods", stdout="Pending")]
    assert detect_command_loop(history, "kubectl get pods", repeat_limit=2) is True


def test_same_command_changed_result_is_progress_not_loop() -> None:
    first = _record("kubectl get pods", stdout="Pending")
    second = _record("kubectl get pods", stdout="Running")
    history = [first, second]
    assert detect_command_loop(history, "kubectl get pods", repeat_limit=2) is False
    assert evaluate_command_progress(history, second)["has_progress"] is True


def test_same_failure_result_is_detected_as_loop() -> None:
    history = [
        _record("mysql -uroot", stderr="Access denied", exit_code=1),
        _record("mysql -uroot", stderr="Access denied", exit_code=1),
    ]
    assert detect_command_loop(history, "mysql -uroot", repeat_limit=2) is True


def test_different_commands_are_not_a_loop() -> None:
    history = [_record("where mysql", stdout="D:\\Mysql\\bin\\mysql.exe")]
    assert detect_command_loop(history, "mysql --version", repeat_limit=2) is False


def test_tool_message_has_matching_assistant_tool_call(monkeypatch) -> None:
    class _Client:
        def tool_call(self, messages, tools, tool_choice):
            return {"id": "call-123", "name": "cmd_execute", "arguments": {"command": "dir", "rationale": "读取"}}

    monkeypatch.setattr("app.agent.workflow.DeepSeekClient", _Client)
    state = initial_state("列出目录")
    workflow = RAGWorkflow()
    decision = workflow._llm_select_tool(state["question"], state)
    state["last_tool_call_id"] = decision["tool_call_id"]
    ReActWorkflow._append_step(state, "cmd_execute", decision["args"], {"success": True, "stdout": "a.txt"})

    user, assistant, tool = state["llm_messages"]
    assert user["role"] == "user"
    assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"] == "call-123"
    assert tool["role"] == "tool"
