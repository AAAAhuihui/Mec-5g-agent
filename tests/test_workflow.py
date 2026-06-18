from __future__ import annotations

from app.agent.classifier import classify_question
from app.agent.query_rewriter import generate_queries
from app.rag.evidence_compressor import compress_evidence


def test_classifier_marks_domain_question_for_retrieval() -> None:
    result = classify_question("NEF 返回成功但流量没有进入 MEC APP，为什么？")
    assert result["need_retrieval"] is True
    assert result["intent"] == "troubleshooting"


def test_query_rewriter_generates_multiple_queries() -> None:
    queries = generate_queries("NEF 返回成功但流量没有进入 MEC APP", "troubleshooting", ["NEF", "MEC APP"])
    assert len(queries) >= 5
    assert any("UPF" in query for query in queries)


def test_evidence_compressor_extracts_facts() -> None:
    docs = [
        {
            "source": "traffic_influence.md",
            "content": "NEF 返回成功不等于 UPF 已经完成用户面分流规则安装。SMF 需要把 PDR/FAR 安装到 UPF。",
        }
    ]
    facts = compress_evidence(docs)
    assert facts
    assert facts[0]["source"] == "traffic_influence.md"

