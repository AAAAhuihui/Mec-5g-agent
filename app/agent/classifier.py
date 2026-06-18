from __future__ import annotations

import re
from typing import Any


DOMAIN_KEYWORDS = [
    "MEC",
    "5G",
    "5GC",
    "NEF",
    "NEF_NBI",
    "Traffic Influence",
    "PFD",
    "PFU",
    "UPF",
    "SMF",
    "AMF",
    "PCF",
    "MEP",
    "MEPM",
    "MECM",
    "MEC APP",
    "N3",
    "N6",
    "PDR",
    "FAR",
    "分流",
    "信令",
    "基站",
    "核心网",
    "边缘计算",
]


def classify_question(question: str) -> dict[str, Any]:
    lowered = question.lower()
    entities = [keyword for keyword in DOMAIN_KEYWORDS if keyword.lower() in lowered]
    need_retrieval = bool(entities)

    if re.search(r"故障|排查|失败|不通|没有|未|原因|为什么|抓包|日志", question):
        intent = "troubleshooting"
    elif re.search(r"流程|信令|步骤|链路|交互", question):
        intent = "signaling_flow"
    elif re.search(r"请求体|payload|json|接口|api|生成", lowered):
        intent = "api_generation"
    elif re.search(r"代码|函数|类|报错|定位|repo|repository", lowered):
        intent = "code_analysis"
    elif re.search(r"是什么|概念|解释|区别|关系|介绍", question):
        intent = "concept_qa"
    else:
        intent = "general"

    return {
        "intent": intent,
        "domain_entities": entities,
        "need_retrieval": need_retrieval,
    }

