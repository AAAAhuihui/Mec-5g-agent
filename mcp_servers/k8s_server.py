from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from mcp.server.fastmcp import FastMCP


MAX_EVENTS = 20
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+|password\s*[:=]\s*|token\s*[:=]\s*|"
    r"api[_-]?key\s*[:=]\s*)([^\s,;]+)"
)

ALLOWED_NAMESPACES = {
    namespace.strip()
    for namespace in os.getenv("K8S_ALLOWED_NAMESPACES", "").split(",")
    if namespace.strip()
}
MAX_LOG_LINES = max(1, int(os.getenv("K8S_LOG_MAX_LINES", "500")))
MAX_LOG_BYTES = max(1024, int(os.getenv("K8S_LOG_MAX_BYTES", "262144")))
MAX_SINCE_SECONDS = max(60, int(os.getenv("K8S_LOG_MAX_SINCE_SECONDS", "86400")))
MCP_HOST = os.getenv("K8S_MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("K8S_MCP_PORT", "8000"))

mcp = FastMCP(
    "k8s-pod-diagnostics",
    instructions=(
        "Read-only Kubernetes pod diagnostics. It never executes commands, mutates resources, "
        "or returns unbounded logs."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def collect_pod_diagnostics(
    namespace: str,
    pod_name: str,
    container: str | None = None,
    since_seconds: int = 3600,
    tail_lines: int = 500,
) -> dict[str, Any]:
    """Collect read-only Pod status, warning events, and current/previous container logs.

    The namespace must be explicitly listed in K8S_ALLOWED_NAMESPACES. Logs are capped,
    timestamped, and redacted before they leave this service.
    """
    _assert_allowed_namespace(namespace)
    core = _core_api()
    pod = core.read_namespaced_pod(name=pod_name, namespace=namespace)
    selected_container = _select_container(pod, container)
    events = core.list_namespaced_event(
        namespace=namespace,
        field_selector=f"involvedObject.name={pod_name}",
    ).items

    logs = [
        _read_log(
            core,
            namespace=namespace,
            pod_name=pod_name,
            container=selected_container,
            stream="current",
            since_seconds=since_seconds,
            tail_lines=tail_lines,
            previous=False,
        )
    ]
    if _container_restart_count(pod, selected_container) > 0:
        logs.append(
            _read_log(
                core,
                namespace=namespace,
                pod_name=pod_name,
                container=selected_container,
                stream="previous",
                since_seconds=since_seconds,
                tail_lines=tail_lines,
                previous=True,
            )
        )

    return {
        "pod": _pod_summary(pod),
        "events": [_event_summary(event) for event in events[-MAX_EVENTS:]],
        "logs": logs,
    }


@lru_cache(maxsize=1)
def _core_api() -> client.CoreV1Api:
    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config(config_file=os.getenv("KUBECONFIG") or None)
    return client.CoreV1Api()


def _assert_allowed_namespace(namespace: str) -> None:
    if not ALLOWED_NAMESPACES:
        raise ValueError("K8S_ALLOWED_NAMESPACES 未配置；服务拒绝读取任何命名空间。")
    if namespace not in ALLOWED_NAMESPACES:
        raise ValueError(f"命名空间 {namespace!r} 不在 K8S_ALLOWED_NAMESPACES 白名单中。")


def _select_container(pod: Any, requested: str | None) -> str:
    containers = [container.name for container in (pod.spec.containers or [])]
    if requested:
        if requested not in containers:
            raise ValueError(f"容器 {requested!r} 不属于 Pod；可选容器：{containers}")
        return requested
    default_container = (pod.metadata.annotations or {}).get(
        "kubectl.kubernetes.io/default-container"
    )
    if default_container in containers:
        return str(default_container)
    if not containers:
        raise ValueError("Pod 没有可读取日志的业务容器。")
    return containers[0]


def _read_log(
    core: client.CoreV1Api,
    *,
    namespace: str,
    pod_name: str,
    container: str,
    stream: str,
    since_seconds: int,
    tail_lines: int,
    previous: bool,
) -> dict[str, Any]:
    try:
        raw = core.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            previous=previous,
            timestamps=True,
            since_seconds=min(max(1, since_seconds), MAX_SINCE_SECONDS),
            tail_lines=min(max(1, tail_lines), MAX_LOG_LINES),
            limit_bytes=MAX_LOG_BYTES,
        )
        text = _redact(str(raw))
        return {
            "container": container,
            "stream": stream,
            "text": text,
            "truncated": len(str(raw).encode("utf-8")) >= MAX_LOG_BYTES,
        }
    except ApiException as error:
        return {
            "container": container,
            "stream": stream,
            "text": "",
            "error": f"Kubernetes API {error.status}: {error.reason}",
            "truncated": False,
        }


def _pod_summary(pod: Any) -> dict[str, Any]:
    statuses = pod.status.container_statuses or []
    return {
        "namespace": pod.metadata.namespace,
        "name": pod.metadata.name,
        "phase": pod.status.phase or "Unknown",
        "node": pod.spec.node_name or "",
        "conditions": [
            {
                "type": condition.type,
                "status": condition.status,
                "reason": condition.reason or "",
                "message": condition.message or "",
            }
            for condition in (pod.status.conditions or [])
        ],
        "containers": [
            {
                "name": status.name,
                "ready": status.ready,
                "restart_count": status.restart_count,
                "state": _container_state(status),
            }
            for status in statuses
        ],
    }


def _container_state(status: Any) -> dict[str, Any]:
    state = status.state
    if state.waiting:
        return {"type": "waiting", "reason": state.waiting.reason or "", "message": state.waiting.message or ""}
    if state.terminated:
        return {
            "type": "terminated",
            "reason": state.terminated.reason or "",
            "exit_code": state.terminated.exit_code,
            "message": state.terminated.message or "",
        }
    return {"type": "running"}


def _container_restart_count(pod: Any, container: str) -> int:
    for status in pod.status.container_statuses or []:
        if status.name == container:
            return int(status.restart_count or 0)
    return 0


def _event_summary(event: Any) -> dict[str, str]:
    timestamp = event.event_time or event.last_timestamp or event.first_timestamp
    return {
        "type": event.type or "Normal",
        "reason": event.reason or "",
        "message": _redact(event.message or ""),
        "timestamp": str(timestamp or ""),
    }


def _redact(text: str) -> str:
    return SENSITIVE_VALUE_PATTERN.sub(r"\1***REDACTED***", text)


def main() -> None:
    # MCP Python SDK 1.x stores host/port in FastMCP settings. Its run()
    # method selects the transport but does not accept host/port keywords.
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
