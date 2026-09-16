from __future__ import annotations

from typing import Any

from app.llm.deepseek_client import DeepSeekClient


ALLOWED_PLAN_TOOLS = {
    "rag_retrieve",
    "web_search",
    "code_search",
    "analyze_pod_bug",
    "cmd_execute",
    "file_read",
    "file_search",
    "file_write",
    "file_delete",
    "finish_task",
}
MAX_PLAN_STEPS = 6


def create_task_plan(question: str) -> list[dict[str, Any]]:
    """Ask the model for a high-level, non-executing task plan with a safe fallback."""
    client = DeepSeekClient()
    plan = client.json_chat(
        [
            {
                "role": "system",
                "content": (
                    "You are a task planner for a bounded agent. Produce JSON only: "
                    '{"steps":[{"goal":"...","tool":"..."}]}. Use 1-6 high-level steps. '
                    "Allowed tools: rag_retrieve, web_search, code_search, analyze_pod_bug, cmd_execute, file_search, file_read, file_write, file_delete, "
                    "finish_task. Do not include exact shell commands. Read-only information gathering comes "
                    "before any cmd_execute step."
                ),
            },
            {"role": "user", "content": question},
        ]
    )
    normalized = _normalize_plan(plan)
    return normalized or _fallback_plan(question)


def _normalize_plan(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("steps"), list):
        return []
    result = []
    for item in value["steps"][:MAX_PLAN_STEPS]:
        if not isinstance(item, dict):
            continue
        goal = str(item.get("goal", "")).strip()
        tool = str(item.get("tool", "")).strip()
        if goal and tool in ALLOWED_PLAN_TOOLS:
            result.append(
                {
                    "index": len(result) + 1,
                    "goal": goal[:300],
                    "tool": tool,
                    "status": "pending",
                }
            )
    return result


def _fallback_plan(question: str) -> list[dict[str, Any]]:
    return [
        {"index": 1, "goal": "分析用户目标并收集必要信息", "tool": "finish_task", "status": "pending"},
    ]
