from __future__ import annotations

from typing import Any

from app.llm.deepseek_client import DeepSeekClient
from app.llm.prompts import ANSWER_SYSTEM_PROMPT


TROUBLESHOOTING_STEPS = [
    "查 NEF_NBI 是否收到 Traffic Influence 请求",
    "查 NEF 是否返回成功以及响应中的关键字段",
    "查 SMF 是否接收并下发分流策略",
    "查 UPF 是否安装对应的 PDR/FAR",
    "查 N3/N6 抓包，确认用户面流量路径",
    "查 MEP Traffic Rule 是否匹配 MEC APP",
    "查 MEC APP Service 是否可达并正常监听",
]


def generate_answer(
    question: str,
    evidence_facts: list[dict[str, Any]],
    intent: str,
    revise: bool = False,
) -> str:
    llm_answer = _llm_generate(question, evidence_facts, intent, revise)
    if llm_answer:
        return llm_answer
    return _fallback_answer(question, evidence_facts, intent, revise)


def _llm_generate(
    question: str,
    evidence_facts: list[dict[str, Any]],
    intent: str,
    revise: bool,
) -> str | None:
    if not evidence_facts:
        return None
    client = DeepSeekClient()
    facts = "\n".join(
        f"- {fact.get('claim')}（来源：{fact.get('source')}）" for fact in evidence_facts
    )
    instruction = "请修正答案，弱化无证据强结论。" if revise else "请生成答案。"
    return client.chat(
        [
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{instruction}\n问题：{question}\n意图：{intent}\n证据事实：\n{facts}\n\n"
                    "固定使用以下结构：\n结论：\n...\n\n依据：\n1. ...\n\n"
                    "详细解释：\n...\n\n涉及组件：\n...\n\n下一步建议：\n..."
                ),
            },
        ],
        temperature=0.2,
    )


def _fallback_answer(
    question: str,
    evidence_facts: list[dict[str, Any]],
    intent: str,
    revise: bool,
) -> str:
    if not evidence_facts:
        return (
            "结论：\n"
            "当前证据不足，不能给出确定的 MEC/5G 领域结论。\n\n"
            "依据：\n"
            "1. 本次检索没有获得可支撑答案的文档片段。\n\n"
            "详细解释：\n"
            "系统没有找到与问题直接相关的 NEF、SMF、UPF、MEP 或 MEC APP 证据。"
            "建议先补充 Traffic Influence、分流策略、PDR/FAR 或现场日志文档后重新提问。\n\n"
            "涉及组件：\n"
            "证据不足，暂不确定。\n\n"
            "下一步建议：\n"
            "导入 data/raw_docs 下的领域文档，或补充 NEF_NBI、SMF、UPF、MEP、MEC APP 相关日志。"
        )

    basis = "\n".join(
        f"{index + 1}. {fact.get('claim')}（来源：{fact.get('source')}）"
        for index, fact in enumerate(evidence_facts[:5])
    )
    components = _extract_components(evidence_facts)

    if intent == "concept_qa":
        conclusion = _concept_conclusion(question, evidence_facts)
        return (
            f"结论：\n{conclusion}\n\n"
            f"依据：\n{basis}\n\n"
            "详细解释：\n"
            "从当前证据看，这类问题应先区分 5GC 网络能力暴露、用户面转发、MEC 平台能力和 MEC 应用服务几个层次。"
            "NEF 负责对外暴露网络能力，SMF 负责会话和用户面规则控制，UPF 负责执行 PDR/FAR 等转发规则，"
            "MEP 提供 MEC 平台能力，MEPM 负责 MEC 平台管理，MEC APP 则是边缘侧承载业务逻辑的应用。\n\n"
            f"涉及组件：\n{components}\n\n"
            "下一步建议：\n"
            "1. 如果要继续细化，可以追问某个组件与 NEF、SMF、UPF 或 MEC APP 的交互关系。\n"
            "2. 如果关注分流链路，可以继续询问 Traffic Influence 到 PDR/FAR 安装的完整流程。"
        )

    conclusion = "结合现有证据，问题更可能出现在 Traffic Influence 下发后的策略传递、UPF 规则安装、N6 分流或 MEP/MEC APP 匹配环节。"
    if revise:
        conclusion = "结合现有证据，只能判断需要重点检查 Traffic Influence 下发后的策略传递、UPF 规则安装、N6 分流或 MEP/MEC APP 匹配环节。"

    steps = TROUBLESHOOTING_STEPS if intent == "troubleshooting" else [
        "对照证据确认关键组件关系",
        "补充缺失接口字段或日志",
        "必要时继续检索更细的流程文档",
    ]
    suggestions = "\n".join(f"{index + 1}. {step}" for index, step in enumerate(steps))

    return (
        f"结论：\n{conclusion}\n\n"
        f"依据：\n{basis}\n\n"
        "详细解释：\n"
        f"针对问题“{question}”，NEF 返回成功通常只能说明北向请求已被接收或初步处理，"
        "还需要继续确认策略是否经 SMF 下发到 UPF、UPF 是否安装 PDR/FAR、N3/N6 流量是否按规则转发，"
        "以及 MEP Traffic Rule 与 MEC APP Service 是否匹配。上述判断均来自当前检索到的证据。\n\n"
        f"涉及组件：\n{components}\n\n"
        f"下一步建议：\n{suggestions}"
    )


def _concept_conclusion(question: str, evidence_facts: list[dict[str, Any]]) -> str:
    question_lower = question.lower()
    if "mepm" in question_lower:
        return "MEPM 在 MEC 系统中主要负责 MEC 平台管理，关注平台实例、能力和生命周期管理，不直接承担用户面分流转发。"
    if "mep" in question_lower:
        return "MEP 在 MEC 系统中主要提供平台能力和流量规则等支撑能力，使 MEC APP 能使用边缘平台服务。"
    if "mec app" in question_lower or "应用" in question:
        return "MEC APP 是部署在边缘侧的业务应用，分流成功后用户流量最终需要到达对应的应用服务。"
    if "upf" in question_lower:
        return "UPF 负责执行用户面转发规则，PDR 用于匹配流量，FAR 用于决定转发动作。"
    if "nef" in question_lower:
        return "NEF 是 5GC 对外暴露网络能力的入口，AF 可通过 NEF/NEF_NBI 提交 Traffic Influence 等请求。"
    return "MEC/5G 分流场景中，各组件按网络能力暴露、策略/会话控制、用户面转发、平台管理和边缘应用服务分工协作。"


def _extract_components(evidence_facts: list[dict[str, Any]]) -> str:
    text = " ".join(str(fact.get("claim", "")) for fact in evidence_facts)
    components = [
        component
        for component in [
            "AF",
            "MEC",
            "NEF_NBI",
            "NEF",
            "SMF",
            "UPF",
            "PDR",
            "FAR",
            "N3",
            "N6",
            "MEPM",
            "MEP",
            "MEC APP",
        ]
        if component.lower() in text.lower()
    ]
    return "、".join(components) if components else "当前证据未明确列出组件"
