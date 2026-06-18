from __future__ import annotations

from pathlib import Path


def search_logs(query: str, log_dir: str) -> list[dict[str, str]]:
    root = Path(log_dir)
    if not root.exists():
        return [{"status": "not_found", "message": f"log_dir not found: {log_dir}"}]
    return [
        {
            "status": "TODO",
            "query": query,
            "log_dir": str(root),
            "message": "日志搜索工具骨架已预留，后续可接入真实日志目录或日志平台。",
        }
    ]

