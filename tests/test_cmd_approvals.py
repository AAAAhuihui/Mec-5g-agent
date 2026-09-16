from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.workflow import RAGWorkflow
from app.tools import cmd_approvals


def _settings(tmp_path):
    return SimpleNamespace(
        cmd_tool_enabled=True,
        cmd_tool_timeout_seconds=30,
        cmd_tool_approval_ttl_seconds=300,
        cmd_tool_allowed_workdir=tmp_path,
        cmd_tool_max_output_chars=1000,
    )


def test_cmd_command_requires_explicit_approval(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cmd_approvals, "settings", _settings(tmp_path))
    approval = cmd_approvals.request_cmd_approval(
        {"command": r'mkdir "%USERPROFILE%\Desktop\qdh"', "rationale": "创建目录"}
    )
    cmd_approvals.bind_cmd_approval(approval["approval_id"], "conversation-a")
    called = []
    monkeypatch.setattr(
        cmd_approvals,
        "_execute_cmd",
        lambda command, workdir: called.append(command)
        or {"status": "completed", "exit_code": 0, "stdout": "", "stderr": "", "error": None},
    )

    result = cmd_approvals.approve_cmd_command(approval["approval_id"], "conversation-a", approved=False)
    assert result["status"] == "rejected"
    assert called == []


def test_cmd_execution_runs_exact_command_once(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cmd_approvals, "settings", _settings(tmp_path))
    approval = cmd_approvals.request_cmd_approval({"command": "dir", "rationale": "列出目录"})
    cmd_approvals.bind_cmd_approval(approval["approval_id"], "conversation-b")
    seen = []
    monkeypatch.setattr(
        cmd_approvals,
        "_execute_cmd",
        lambda command, workdir: seen.append(command)
        or {"status": "completed", "exit_code": 0, "stdout": "", "stderr": "", "error": None},
    )
    result = cmd_approvals.approve_cmd_command(approval["approval_id"], "conversation-b", approved=True)
    assert seen == ["dir"]
    assert result["exit_code"] == 0
    with pytest.raises(cmd_approvals.CmdApprovalError):
        cmd_approvals.approve_cmd_command(approval["approval_id"], "conversation-b", approved=True)


def test_cmd_rejects_non_cmd_directory_syntax(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cmd_approvals, "settings", _settings(tmp_path))
    with pytest.raises(cmd_approvals.CmdApprovalError, match="CMD"):
        cmd_approvals.request_cmd_approval(
            {"command": "mkdir -p ~/Desktop/qdh", "rationale": "创建目录"}
        )


def test_read_only_cmd_allowlist_is_conservative() -> None:
    assert cmd_approvals.is_read_only_cmd(r'dir "%USERPROFILE%\Desktop"') is True
    assert cmd_approvals.is_read_only_cmd("kubectl logs api-123") is True
    assert cmd_approvals.is_read_only_cmd(r'mkdir "%USERPROFILE%\Desktop\qdh"') is False
    assert cmd_approvals.is_read_only_cmd("dir & del data.txt") is False


def test_cmd_backend_invokes_cmd_exe(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cmd_approvals, "settings", _settings(tmp_path))
    monkeypatch.setattr(cmd_approvals.shutil, "which", lambda name: r"C:\Windows\System32\cmd.exe")
    captured = []
    monkeypatch.setattr(
        cmd_approvals.subprocess,
        "run",
        lambda command, **kwargs: captured.append(command)
        or SimpleNamespace(returncode=0, stdout=None, stderr=None),
    )
    result = cmd_approvals._execute_cmd("dir", tmp_path)
    assert captured == ["dir"]
    assert result["status"] == "completed"


def test_cmd_environment_keeps_pip_tls_and_proxy_configuration(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", r"C:\certs\corp-ca.pem")
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirror.example/simple")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-be-forwarded")

    environment = cmd_approvals._minimal_environment()

    assert environment["HTTPS_PROXY"] == "http://proxy.example:8080"
    assert environment["REQUESTS_CA_BUNDLE"] == r"C:\certs\corp-ca.pem"
    assert environment["PIP_INDEX_URL"] == "https://mirror.example/simple"
    assert "UNRELATED_SECRET" not in environment


def test_workflow_routes_cmd_tool_to_approval(monkeypatch) -> None:
    workflow = RAGWorkflow()
    workflow._llm_select_tool = lambda question, state: {
        "tool": "cmd_execute",
        "args": {"command": "dir", "rationale": "列出目录"},
        "reason": "explicit request",
        "source": "test",
    }
    monkeypatch.setattr(
        "app.agent.workflow.request_cmd_approval",
        lambda args: {
            "approval_id": "pending-1", "status": "pending", "command": args["command"],
            "rationale": args["rationale"], "working_dir": "C:\\safe", "created_at": "", "expires_at": "",
            "exit_code": None, "stdout": "", "stderr": "", "error": None,
        },
    )
    state = workflow.run("请执行 dir")
    assert state["selected_tool"] == "cmd_execute"
    assert state["approval"]["status"] == "pending"
