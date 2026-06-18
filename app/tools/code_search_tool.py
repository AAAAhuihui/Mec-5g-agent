from __future__ import annotations

from pathlib import Path


def search_code(query: str, repo_path: str) -> list[dict[str, str]]:
    root = Path(repo_path)
    if not root.exists():
        return [{"status": "not_found", "message": f"repo_path not found: {repo_path}"}]
    return [
        {
            "status": "TODO",
            "query": query,
            "repo_path": str(root),
            "message": "代码搜索工具骨架已预留，后续可接入 ripgrep 或代码索引。",
        }
    ]

