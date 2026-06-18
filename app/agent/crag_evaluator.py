from __future__ import annotations

from typing import Any

from app.agent.classifier import DOMAIN_KEYWORDS
from app.llm.deepseek_client import DeepSeekClient
from app.llm.prompts import CRAG_SYSTEM_PROMPT


def crag_evaluate(question: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
    if not docs:
        return {
            "retrieval_grade": "empty",
            "score": 0.0,
            "reason": "没有检索到相关文档片段。",
            "missing_evidence": [],
            "next_queries": [question],
        }

    rule_result = _rule_evaluate(question, docs)
    llm_result = _llm_evaluate(question, docs)
    return _merge_evaluations(rule_result, llm_result)


def _merge_evaluations(
    rule_result: dict[str, Any],
    llm_result: dict[str, Any] | None,
) -> dict[str, Any]:
    if not llm_result:
        return rule_result

    rule_grade = rule_result.get("retrieval_grade")
    llm_grade = llm_result.get("retrieval_grade")
    llm_missing = llm_result.get("missing_evidence") or []

    # 对短概念问答，LLM 有时会把“可回答但不完整”保守判成 ambiguous。
    # 如果规则侧已经覆盖核心实体，且 LLM 没指出明确缺失证据，则按 correct 输出。
    if rule_grade == "correct" and llm_grade == "ambiguous" and not llm_missing:
        merged = dict(rule_result)
        merged["reason"] = f"{rule_result['reason']} LLM 评估偏保守，但未指出明确缺失证据。"
        return merged

    return llm_result


def _rule_evaluate(question: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
    question_lower = question.lower()
    evidence_text = "\n".join(str(doc.get("content", "")) for doc in docs).lower()
    expected = [term for term in DOMAIN_KEYWORDS if term.lower() in question_lower]
    covered = [term for term in expected if term.lower() in evidence_text]
    score = sum(float(doc.get("score", 0.0)) for doc in docs[:3]) / max(min(len(docs), 3), 1)

    if expected:
        coverage = len(covered) / len(expected)
    else:
        coverage = 1.0 if score > 0.18 else 0.0

    missing = [term for term in expected if term not in covered]
    if score < 0.05 and coverage < 0.3:
        grade = "incorrect"
        reason = "检索结果与问题中的核心实体重合度很低。"
    elif coverage < 0.6:
        grade = "ambiguous"
        reason = "检索结果只覆盖了部分关键实体或组件。"
    else:
        grade = "correct"
        reason = "检索结果覆盖了问题中的主要实体，可支持生成答案。"

    next_queries = []
    if missing:
        next_queries.append(" ".join(missing) + " MEC 5G 分流 证据")
    if grade in {"incorrect", "ambiguous"}:
        next_queries.append(question + " 排查 原因 证据")

    return {
        "retrieval_grade": grade,
        "score": round(float(score), 4),
        "reason": reason,
        "missing_evidence": missing,
        "next_queries": next_queries,
    }


def _llm_evaluate(question: str, docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    client = DeepSeekClient()
    evidence = "\n\n".join(
        f"[{index + 1}] {doc.get('source')}: {doc.get('content')}"
        for index, doc in enumerate(docs[:5])
    )
    payload = client.json_chat(
        [
            {"role": "system", "content": CRAG_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "请输出 JSON，字段为 retrieval_grade, score, reason, "
                    "missing_evidence, next_queries。\n"
                    f"问题：{question}\n证据：\n{evidence}"
                ),
            },
        ]
    )
    if not payload:
        return None
    grade = payload.get("retrieval_grade")
    if grade not in {"correct", "ambiguous", "incorrect", "empty"}:
        return None
    return {
        "retrieval_grade": grade,
        "score": float(payload.get("score", 0.0)),
        "reason": str(payload.get("reason", "")),
        "missing_evidence": list(payload.get("missing_evidence", [])),
        "next_queries": list(payload.get("next_queries", [])),
    }
