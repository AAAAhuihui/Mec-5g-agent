from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.tools.mcp_http_client import MCPClientError, call_mcp_tool


IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SIGNAL_PATTERN = re.compile(
    r"\b(error|exception|fatal|panic|traceback|oomkilled|refused|timeout|failed|crash)\b",
    flags=re.IGNORECASE,
)


def run_pod_diagnostics(tool_args: dict[str, Any]) -> dict[str, Any]:
    args = _normalize_args(tool_args)
    if isinstance(args, str):
        return {"docs": [], "facts": [], "answer": args, "query": ""}

    try:
        payload = call_mcp_tool("collect_pod_diagnostics", args)
    except MCPClientError as error:
        return {
            "docs": [],
            "facts": [],
            "answer": f"无法采集 Pod 诊断信息：{error}",
            "query": f"{args['namespace']}/{args['pod_name']}",
        }

    docs, facts = _diagnostics_to_evidence(payload)
    target = f"{args['namespace']}/{args['pod_name']}"
    return {
        "docs": docs,
        "facts": facts,
        "answer": f"未从 {target} 获取到可供分析的诊断证据。",
        "query": target,
    }


def _normalize_args(tool_args: dict[str, Any]) -> dict[str, Any] | str:
    namespace = str(tool_args.get("namespace", "")).strip()
    pod_name = str(tool_args.get("pod_name", "")).strip()
    container = str(tool_args.get("container", "")).strip()
    if not _is_identifier(namespace) or not _is_identifier(pod_name):
        return "Pod 诊断需要合法的 namespace 和 pod_name。"
    if container and not _is_identifier(container):
        return "container 必须是合法的 Kubernetes 容器名称。"

    try:
        since_seconds = int(tool_args.get("since_seconds", 3600))
        tail_lines = int(tool_args.get("tail_lines", settings.k8s_diagnostics_max_log_lines))
    except (TypeError, ValueError):
        return "since_seconds 和 tail_lines 必须是整数。"
    if since_seconds < 1 or tail_lines < 1:
        return "since_seconds 和 tail_lines 必须大于 0。"

    return {
        "namespace": namespace,
        "pod_name": pod_name,
        "container": container or None,
        "since_seconds": min(since_seconds, settings.k8s_diagnostics_max_since_seconds),
        "tail_lines": min(tail_lines, settings.k8s_diagnostics_max_log_lines),
    }


def _diagnostics_to_evidence(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    pod = payload.get("pod") if isinstance(payload.get("pod"), dict) else {}
    namespace = str(pod.get("namespace", "unknown"))
    pod_name = str(pod.get("name", "unknown"))
    base_source = f"k8s://{namespace}/{pod_name}"
    docs: list[dict[str, Any]] = []
    facts: list[dict[str, str]] = []

    status_claim = (
        f"Pod {namespace}/{pod_name} phase={pod.get('phase', 'Unknown')}; "
        f"conditions={pod.get('conditions', [])}; containers={pod.get('containers', [])}."
    )
    docs.append(_doc(status_claim, f"{base_source}/status", "pod_status"))
    facts.append({"claim": status_claim, "source": f"{base_source}/status"})

    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    for index, event in enumerate(events[:20], start=1):
        if not isinstance(event, dict):
            continue
        claim = (
            f"Kubernetes Event {event.get('type', 'Normal')}/{event.get('reason', 'Unknown')}: "
            f"{event.get('message', '')}"
        ).strip()
        source = f"{base_source}/events#{index}"
        docs.append(_doc(claim, source, "pod_event"))
        if event.get("type") == "Warning" or SIGNAL_PATTERN.search(claim):
            facts.append({"claim": claim, "source": source})

    logs = payload.get("logs") if isinstance(payload.get("logs"), list) else []
    for log in logs:
        if not isinstance(log, dict):
            continue
        stream = str(log.get("stream", "current"))
        container = str(log.get("container", "unknown"))
        lines = str(log.get("text", "")).splitlines()
        for line_no, line in _select_log_lines(lines):
            source = f"{base_source}/{container}/{stream}#L{line_no}"
            claim = f"{container} {stream} log: {line}"
            docs.append(_doc(claim, source, "pod_log"))
            facts.append({"claim": claim, "source": source})

    return docs, facts


def _select_log_lines(lines: list[str], max_lines: int = 16) -> list[tuple[int, str]]:
    signals = [
        (index, line.strip())
        for index, line in enumerate(lines, start=1)
        if line.strip() and SIGNAL_PATTERN.search(line)
    ]
    if signals:
        return signals[-max_lines:]
    return [
        (index, line.strip())
        for index, line in list(enumerate(lines, start=1))[-max_lines:]
        if line.strip()
    ]


def _doc(content: str, source: str, evidence_type: str) -> dict[str, Any]:
    return {
        "content": content,
        "source": source,
        "score": 1.0,
        "metadata": {"tool": "analyze_pod_bug", "evidence_type": evidence_type},
    }


def _is_identifier(value: str) -> bool:
    return bool(value and len(value) <= 253 and IDENTIFIER_PATTERN.fullmatch(value))
