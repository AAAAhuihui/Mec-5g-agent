from __future__ import annotations

from typing import Any

from app.llm.deepseek_client import DeepSeekClient
from app.llm.prompts import SELF_CHECK_SYSTEM_PROMPT


STRONG_WORDS = ["一定", "必然", "肯定", "完全", "绝对"]
TROUBLESHOOTING_POINTS = ["NEF", "SMF", "UPF", "PDR", "FAR", "N3", "N6", "MEP", "MEC APP"]


def self_check(
    question: str,
    draft_answer: str,
    evidence_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    if not evidence_facts:
        return {
            "faithfulness": "unsupported",
            "unsupported_claims": ["缺少证据事实，不能输出确定结论。"],
            "missing_points": [],
            "need_more_retrieval": True,
            "action": "retrieve_more",
        }

    llm_result = _llm_check(question, draft_answer, evidence_facts)
    if llm_result:
        return llm_result
    return _rule_check(question, draft_answer, evidence_facts)


def _rule_check(
    question: str,
    draft_answer: str,
    evidence_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    unsupported: list[str] = []
    missing: list[str] = []
    evidence_text = " ".join(str(fact.get("claim", "")) for fact in evidence_facts)

    if any(word in draft_answer for word in STRONG_WORDS) and len(evidence_facts) < 3:
        unsupported.append("答案包含强结论词，但证据数量不足。")

    if any(word in question for word in ["故障", "失败", "没有", "为什么", "原因"]):
        for point in TROUBLESHOOTING_POINTS:
            if point.lower() in question.lower() or point.lower() in evidence_text.lower():
                if point.lower() not in draft_answer.lower():
                    missing.append(point)

    if unsupported:
        faithfulness = "partial"
        action = "revise_answer"
    elif missing:
        faithfulness = "partial"
        action = "revise_answer"
    else:
        faithfulness = "supported"
        action = "final"

    return {
        "faithfulness": faithfulness,
        "unsupported_claims": unsupported,
        "missing_points": missing,
        "need_more_retrieval": faithfulness == "unsupported",
        "action": action,
    }


def _llm_check(
    question: str,
    draft_answer: str,
    evidence_facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    client = DeepSeekClient()
    facts = "\n".join(f"- {fact.get('claim')}（{fact.get('source')}）" for fact in evidence_facts)
    payload = client.json_chat(
        [
            {"role": "system", "content": SELF_CHECK_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "请输出 JSON，字段为 faithfulness, unsupported_claims, missing_points, "
                    "need_more_retrieval, action。\n"
                    f"问题：{question}\n答案：{draft_answer}\n证据：\n{facts}"
                ),
            },
        ]
    )
    if not payload:
        return None
    faithfulness = payload.get("faithfulness")
    action = payload.get("action")
    if faithfulness not in {"supported", "partial", "unsupported"}:
        return None
    if action not in {"final", "revise_answer", "retrieve_more"}:
        return None
    return {
        "faithfulness": faithfulness,
        "unsupported_claims": list(payload.get("unsupported_claims", [])),
        "missing_points": list(payload.get("missing_points", [])),
        "need_more_retrieval": bool(payload.get("need_more_retrieval", False)),
        "action": action,
    }

