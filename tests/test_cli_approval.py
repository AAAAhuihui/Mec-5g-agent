from __future__ import annotations

from types import SimpleNamespace

import cli


class _Client:
    def __init__(self) -> None:
        self.calls = []

    def post(self, path, json):
        self.calls.append((path, json))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "completed", "exit_code": 0, "stdout": "", "stderr": "", "error": None},
        )


def test_cli_y_confirms_pending_cmd_command(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    cli._confirm_cmd_approval(client, "session-1", {"approval_id": "approval-1"})
    assert client.calls == [
        ("/cmd/approve", {"conversation_id": "session-1", "approval_id": "approval-1", "approved": True})
    ]


def test_cli_enter_rejects_pending_cmd_command(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    cli._confirm_cmd_approval(client, "session-1", {"approval_id": "approval-1"})
    assert client.calls[0][1]["approved"] is False
