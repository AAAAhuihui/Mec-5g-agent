from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools import file_tools


def _settings(root):
    return SimpleNamespace(
        file_tool_enabled=True,
        file_tool_allowed_root=root,
        file_tool_max_read_chars=1000,
        file_tool_max_write_chars=10000,
        cmd_tool_approval_ttl_seconds=300,
    )


def test_file_write_requires_approval_then_writes_utf8(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(file_tools, "settings", _settings(tmp_path))
    approval = file_tools.request_file_write_approval(
        {"path": "tetris/index.html", "content": "<h1>Tetris</h1>", "rationale": "创建游戏页面"}
    )
    assert approval["approval_type"] == "file_write"
    assert not (tmp_path / "tetris" / "index.html").exists()

    file_tools.bind_file_approval(approval["approval_id"], "session-1")
    result = file_tools.approve_file_write(approval["approval_id"], "session-1", approved=True)
    assert result["success"] is True
    assert (tmp_path / "tetris" / "index.html").read_text(encoding="utf-8") == "<h1>Tetris</h1>"


def test_file_read_is_bounded_to_allowed_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(file_tools, "settings", _settings(tmp_path))
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    assert file_tools.read_file({"path": "note.txt"})["content"] == "hello"
    with pytest.raises(file_tools.FileToolError):
        file_tools.read_file({"path": "..\\outside.txt"})


def test_file_search_finds_named_text_file_under_allowed_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(file_tools, "settings", _settings(tmp_path))
    target = tmp_path / "games" / "tetris.py"
    target.parent.mkdir()
    target.write_text("print('tetris')", encoding="utf-8")

    result = file_tools.find_files({"query": "tetris"})

    assert result["success"] is True
    assert result["files"] == [{"path": str(target), "size": len("print('tetris')")}]


def test_existing_file_requires_explicit_overwrite(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(file_tools, "settings", _settings(tmp_path))
    target = tmp_path / "index.html"
    target.write_text("old", encoding="utf-8")
    approval = file_tools.request_file_write_approval(
        {"path": "index.html", "content": "new", "rationale": "更新页面"}
    )
    file_tools.bind_file_approval(approval["approval_id"], "session-2")
    result = file_tools.approve_file_write(approval["approval_id"], "session-2", approved=True)
    assert result["status"] == "failed"
    assert target.read_text(encoding="utf-8") == "old"


def test_file_delete_requires_approval_then_removes_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(file_tools, "settings", _settings(tmp_path))
    target = tmp_path / "obsolete.txt"
    target.write_text("old", encoding="utf-8")
    approval = file_tools.request_file_delete_approval({"path": "obsolete.txt", "rationale": "删除旧文件"})
    assert target.exists()
    file_tools.bind_file_approval(approval["approval_id"], "session-delete")

    result = file_tools.approve_file_operation(approval["approval_id"], "session-delete", approved=True)

    assert result["approval_type"] == "file_delete"
    assert result["status"] == "completed"
    assert not target.exists()
