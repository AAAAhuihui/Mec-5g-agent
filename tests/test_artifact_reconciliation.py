from __future__ import annotations

from app.agent import mysql_memory_store


def test_reconcile_removes_only_artifacts_whose_files_are_missing(monkeypatch, tmp_path) -> None:
    existing = tmp_path / "kept.py"
    existing.write_text("ok", encoding="utf-8")
    missing = tmp_path / "removed.py"
    deleted: list[str] = []
    monkeypatch.setattr(
        mysql_memory_store,
        "list_session_artifacts",
        lambda session_id, limit: [{"file_path": str(existing)}, {"file_path": str(missing)}],
    )
    monkeypatch.setattr(
        mysql_memory_store,
        "delete_session_artifact_by_path",
        lambda session_id, path: deleted.append(path) or True,
    )

    removed = mysql_memory_store.reconcile_session_artifacts("session-1")

    assert removed == [str(missing)]
    assert deleted == [str(missing)]
