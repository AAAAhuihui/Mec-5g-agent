from app.routers import cmd as cmd_router
from app.schemas import CmdApprovalRequest


def test_cmd_resume_persists_assistant_result_and_refreshes_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        cmd_router,
        "approve_cmd_command",
        lambda *args: {
            "approval_id": "approval-1", "status": "completed", "command": "dir",
            "working_dir": "C:\\safe", "expires_at": "", "exit_code": 0,
            "stdout": "file.txt", "stderr": "", "error": None,
        },
    )
    monkeypatch.setattr(
        cmd_router,
        "get_react_task_by_approval",
        lambda approval_id: {"state": {"task_id": "task-1"}},
    )
    monkeypatch.setattr(
        cmd_router,
        "resume_react_workflow",
        lambda state, result: {
            "task_id": "task-1", "task_status": "completed", "approval": {},
            "final_answer": "目录中包含 file.txt。", "intent": "general",
            "reranked_docs": [{"source": "cmd://dir"}],
        },
    )
    saved = []
    messages = []
    monkeypatch.setattr(cmd_router, "save_react_task", lambda session_id, state: saved.append((session_id, state)))
    monkeypatch.setattr(cmd_router, "append_message", lambda *args, **kwargs: messages.append((args, kwargs)))

    response = cmd_router.approve_cmd(
        CmdApprovalRequest(conversation_id="session-1", approval_id="approval-1", approved=True)
    )

    assert response.answer == "目录中包含 file.txt。"
    assert saved[0][0] == "session-1"
    assert messages[0][0] == ("session-1", "assistant", "目录中包含 file.txt。")
    assert messages[0][1]["evidence_sources"] == ["cmd://dir"]
