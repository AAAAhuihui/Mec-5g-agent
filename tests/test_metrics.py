from __future__ import annotations

from app.metrics import collect_project_metrics, format_project_metrics


def test_project_metric_verification_passes() -> None:
    report = collect_project_metrics()

    assert report["status"] == "passed"
    assert {check["name"] for check in report["checks"]} == {
        "document_chunking",
        "rag_retrieval_bound",
        "react_execution_bounds",
        "duplicate_command_guard",
        "memory_context_bounds",
        "k8s_mcp_data_bounds",
        "k8s_rbac_least_privilege",
    }


def test_metric_report_keeps_unmeasured_quality_metrics_explicit() -> None:
    report = collect_project_metrics()
    text = format_project_metrics(report)

    assert "RAG accuracy" in "\n".join(report["not_measured"])
    assert "Project metric verification: PASSED" in text
