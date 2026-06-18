from __future__ import annotations

from app.agent.memory import ConversationMemory
from app.llm.deepseek_client import DeepSeekClient


FOLLOW_UP_MARKERS = [
    "它",
    "这个",
    "那个",
    "上述",
    "刚才",
    "前面",
    "那",
    "继续",
    "先查哪里",
    "下一步",
    "有关系吗",
]


def build_standalone_question(question: str, memory: ConversationMemory) -> str:
    if not memory.messages or not _looks_like_follow_up(question):
        return question

    llm_result = _llm_contextualize(question, memory)
    if llm_result:
        return llm_result
    return _rule_contextualize(question, memory)


def _looks_like_follow_up(question: str) -> bool:
    short_question = len(question.strip()) <= 30
    has_marker = any(marker in question for marker in FOLLOW_UP_MARKERS)
    return short_question or has_marker


def _llm_contextualize(question: str, memory: ConversationMemory) -> str | None:
    client = DeepSeekClient()
    history = "\n".join(f"{message.role}: {message.content}" for message in memory.messages[-6:])
    result = client.chat(
        [
            {
                "role": "system",
                "content": "你负责把多轮对话中的追问改写成可独立检索的完整中文问题，只输出改写后的问题。",
            },
            {"role": "user", "content": f"历史对话：\n{history}\n\n当前问题：{question}"},
        ],
        temperature=0.0,
    )
    return result.strip() if result else None


def _rule_contextualize(question: str, memory: ConversationMemory) -> str:
    last_user_question = ""
    for message in reversed(memory.messages):
        if message.role == "user":
            last_user_question = message.content
            break

    if not last_user_question:
        return question
    return f"在上一轮问题“{last_user_question}”的上下文中，{question}"

