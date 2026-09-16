"""Deterministic verification for project configuration and safety metrics.

This module intentionally verifies bounded behaviours implemented in code.  It
does not claim quality metrics such as RAG accuracy or diagnosis success rate,
which require a labelled evaluation dataset and a reproducible runtime setup.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.agent.memory import (
    RECENT_MESSAGE_LIMIT,
    RECENT_OPERATION_LIMIT,
    RECENT_TURN_COUNT,
    SUMMARY_MAX_CHARS,
)
from app.config import settings
from app.rag.text_splitter import count_tokens, split_text
from app.tools.command_guard import build_command_record, detect_command_loop


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def collect_project_metrics() -> dict[str, Any]:
    """Return a machine-readable report of verifiable project metrics."""
    checks = [
        _check_document_chunking(),
        _check_rag_bounds(),
        _check_react_bounds(),
        _check_command_loop_guard(),
        _check_memory_bounds(),
        _check_k8s_mcp_bounds(),
        _check_k8s_rbac_policy(),
    ]
    return {
        "scope": "configuration_and_behaviour_bounds",
        "status": "passed" if all(check["passed"] for check in checks) else "failed",
        "checks": checks,
        "not_measured": [
            "RAG accuracy / Recall@K (requires labelled questions and expected sources)",
            "Agent diagnosis success rate (requires reproducible fault scenarios)",
            "Latency, throughput, token usage and cost (requires a running deployment)",
        ],
    }


def format_project_metrics(report: dict[str, Any]) -> str:
    """Render the verification report for terminal use."""
    lines = [
        "Project metric verification: " + str(report["status"]).upper(),
        "Scope: " + str(report["scope"]),
        "",
    ]
    for check in report["checks"]:
        marker = "PASS" if check["passed"] else "FAIL"
        lines.append(f"[{marker}] {check['name']}: {check['details']}")
    lines.extend(["", "Not measured automatically:"])
    lines.extend(f"- {item}" for item in report["not_measured"])
    return "\n".join(lines)


def _check_document_chunking() -> dict[str, Any]:
    source = "The NEF shall expose the resource to the AF. " * 200
    chunks = split_text(source)
    token_counts = [count_tokens(chunk) for chunk in chunks]
    passed = len(chunks) > 1 and max(token_counts) <= settings.rag_chunk_max_tokens
    return _result(
        "document_chunking",
        passed,
        chunking="section-token-v2",
        max_tokens=settings.rag_chunk_max_tokens,
        overlap_tokens=settings.rag_chunk_overlap_tokens,
        produced_chunks=len(chunks),
        observed_max_tokens=max(token_counts),
    )


def _check_rag_bounds() -> dict[str, Any]:
    passed = settings.top_k > 0
    return _result("rag_retrieval_bound", passed, top_k=settings.top_k)


def _check_react_bounds() -> dict[str, Any]:
    values = {
        "max_steps": settings.react_max_steps,
        "max_tool_calls": settings.react_max_tool_calls,
        "max_command_approvals": settings.react_max_cmd_approvals,
        "max_file_approvals": settings.react_max_file_approvals,
        "same_command_retry_limit": settings.react_max_same_command_retries,
        "max_no_progress_steps": settings.react_max_no_progress_steps,
    }
    return _result("react_execution_bounds", all(value > 0 for value in values.values()), **values)


def _check_command_loop_guard() -> dict[str, Any]:
    first = build_command_record(
        "  DIR  ", "C:\\safe", {"status": "completed", "exit_code": 0, "stdout": "same"}
    )
    second = build_command_record(
        "dir", "C:\\safe", {"status": "completed", "exit_code": 0, "stdout": "same"}
    )
    blocked = detect_command_loop(
        [first, second], "Dir", repeat_limit=settings.react_max_same_command_retries
    )
    return _result(
        "duplicate_command_guard",
        blocked,
        normalized_command=first["normalized_command"],
        identical_result_attempts=2,
        third_attempt_blocked=blocked,
    )


def _check_memory_bounds() -> dict[str, Any]:
    passed = (
        RECENT_TURN_COUNT == 3
        and RECENT_MESSAGE_LIMIT == 6
        and RECENT_OPERATION_LIMIT == 3
        and SUMMARY_MAX_CHARS == 6000
    )
    return _result(
        "memory_context_bounds",
        passed,
        recent_turns=RECENT_TURN_COUNT,
        recent_messages=RECENT_MESSAGE_LIMIT,
        recent_operations=RECENT_OPERATION_LIMIT,
        summary_max_chars=SUMMARY_MAX_CHARS,
        important_event_limit=10,
        important_event_min_importance=0.7,
        retained_event_limit=200,
    )


def _check_k8s_mcp_bounds() -> dict[str, Any]:
    content = _read_project_file("mcp_servers/k8s_server.py")
    values = {
        "max_events": _assignment_value(content, "MAX_EVENTS"),
        "max_log_lines": _env_default_value(content, "K8S_LOG_MAX_LINES"),
        "max_log_bytes": _env_default_value(content, "K8S_LOG_MAX_BYTES"),
        "max_since_seconds": _env_default_value(content, "K8S_LOG_MAX_SINCE_SECONDS"),
    }
    passed = values == {
        "max_events": 20,
        "max_log_lines": 500,
        "max_log_bytes": 262144,
        "max_since_seconds": 86400,
    }
    return _result("k8s_mcp_data_bounds", passed, **values)


def _check_k8s_rbac_policy() -> dict[str, Any]:
    content = _read_project_file("deploy/k8s-mcp-rbac.example.yaml").replace(" ", "").replace("\n", "")
    resources_ok = 'resources:["pods","pods/log","events"]' in content
    verbs_ok = 'verbs:["get","list"]' in content
    forbidden = ("exec", "delete", "patch", "update", "secrets")
    forbidden_absent = all(item not in content for item in forbidden)
    return _result(
        "k8s_rbac_least_privilege",
        resources_ok and verbs_ok and forbidden_absent,
        allowed_resources=["pods", "pods/log", "events"],
        allowed_verbs=["get", "list"],
        forbidden_resources_or_verbs_absent=forbidden_absent,
    )


def _result(name: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"name": name, "passed": passed, "details": details}


def _read_project_file(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def _assignment_value(content: str, name: str) -> int:
    match = re.search(rf"^{re.escape(name)}\s*=\s*(\d+)\s*$", content, flags=re.MULTILINE)
    if not match:
        return -1
    return int(match.group(1))


def _env_default_value(content: str, name: str) -> int:
    match = re.search(rf'os\.getenv\("{re.escape(name)}",\s*"(\d+)"\)', content)
    if not match:
        return -1
    return int(match.group(1))
