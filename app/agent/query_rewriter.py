from __future__ import annotations


def generate_queries(question: str, intent: str, domain_entities: list[str]) -> list[str]:
    queries = [question]
    lowered = question.lower()

    if "nef" in lowered or "traffic influence" in lowered or "分流" in question:
        queries.extend(
            [
                "NEF_NBI Traffic Influence 分流策略 下发 流程",
                "NEF 返回成功 UPF 未生效 原因",
                "SMF UPF PDR FAR 策略安装",
                "UPF N6 本地分流 MEC APP",
                "MEP Traffic Rule MEC APP 流量规则",
            ]
        )

    if intent == "troubleshooting":
        queries.extend(
            [
                "NEF_NBI 日志 NEF 响应 SMF 策略 UPF PDR FAR N3 N6 抓包",
                "MEC 分流失败 排查 路径 MEP MEC APP Service",
            ]
        )
    elif intent == "signaling_flow":
        queries.append("AF NEF SMF UPF MEP MEC APP Traffic Influence 信令流程")
    elif intent == "concept_qa" and domain_entities:
        queries.append(" ".join(domain_entities) + " 关系 概念 MEC 5G")

    deduped: list[str] = []
    for query in queries:
        if query and query not in deduped:
            deduped.append(query)
    return deduped[:8]

