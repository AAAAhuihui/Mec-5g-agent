from __future__ import annotations


def query_pods(namespace: str) -> list[dict[str, str]]:
    return [
        {
            "namespace": namespace,
            "status": "TODO",
            "message": "K8s 工具骨架已预留，后续可接入 kubernetes Python client。",
        }
    ]


def query_logs(namespace: str, pod_name: str) -> dict[str, str]:
    return {
        "namespace": namespace,
        "pod_name": pod_name,
        "status": "TODO",
        "logs": "",
        "message": "当前为 mock 返回，后续可查询真实 Pod 日志。",
    }

