from __future__ import annotations

import json

from app.agent.memory import ConversationMemory
from app.agent.memory_repositories import EventRepository
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
    if not memory.messages and not memory.summary and not memory.artifacts:
        return question

    llm_result = _llm_contextualize(question, memory)
    if llm_result:
        return llm_result
    if not _looks_like_follow_up(question):
        return question
    if memory.artifacts:
        return f"{question}\n\n当前会话已知本地产物：\n{_format_artifacts(memory)}"
    return _rule_contextualize(question, memory)


def build_memory_context(
    memory: ConversationMemory,
    question: str,
    event_repository: EventRepository | None = None,
) -> str:
    """Build the bounded, ranked memory view used by context rewriting.

    The event repository performs the importance/relevance/recency ranking, so
    this builder never loads an unbounded event history into the LLM context.
    """
    repository = event_repository or EventRepository()
    try:
        events = repository.get_important_events(
            memory.conversation_id,
            query=question,
            limit=10,
            min_importance=0.7,
        )
    except Exception:
        events = []
    return "\n\n".join(
        [
            "semantic_summary:\n" + _format_summary(memory.semantic_summary, memory.summary),
            "task_summary:\n" + _format_summary(memory.task_summary, "{}"),
            "recent_messages:\n" + _format_recent_messages(memory),
            "important_events:\n" + _format_events(events),
            "recent_operations:\n" + (_format_operations(memory) or "none"),
            "artifacts:\n" + (_format_artifacts(memory) or "none"),
        ]
    )


def build_detail_followup_question(question: str, memory: ConversationMemory) -> str:
    """Make a detail-only follow-up deterministic instead of trusting rewrite quality."""
    messages = memory.messages
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != "assistant" or not message.content.strip():
            continue
        if "任务已阻塞" in message.content:
            continue
        previous_question = ""
        for earlier in reversed(messages[:index]):
            if earlier.role == "user":
                previous_question = earlier.content
                break
        return (
            "用户要求对上一轮回答进行更详细的解释。必须围绕提供的上一轮问题和回答展开，"
            "不要改换为无关的 MEC/5G 主题。\n\n"
            f"上一轮用户问题：\n{_clip_context(previous_question, 3000)}\n\n"
            f"上一轮回答：\n{_clip_context(message.content, 6000)}\n\n"
            f"当前追问：\n{question}"
        )
    return build_standalone_question(question, memory)


def _looks_like_follow_up(question: str) -> bool:
    short_question = len(question.strip()) <= 30
    has_marker = any(marker in question for marker in FOLLOW_UP_MARKERS)
    return short_question or has_marker


def _llm_contextualize(question: str, memory: ConversationMemory) -> str | None:
    client = DeepSeekClient()
    memory_context = build_memory_context(memory, question)
    history = "\n".join(
        f"{message.role}: {message.content}" for message in memory.messages[-10:]
    )
    operations = _format_operations(memory)
    artifacts = _format_artifacts(memory)
    result = client.chat(
        [
            {
                "role": "system",
                "content": f"Bounded structured memory for this request:\n{memory_context}",
            },
            {
                "role": "system",
                "content": f"Known session artifacts. Preserve exact paths when relevant: {artifacts or 'none'}",
            },
            {
                "role": "system",
                "content": (
                    "你负责根据多轮会话上下文，把当前问题改写成可独立检索、可执行的完整中文问题。"
                    "上下文包括历史摘要、最近5轮详细对话、最近3轮工具/检索操作记录。"
                    "如果当前问题与历史无关，直接原样返回当前问题。"
                    "如果当前问题提到“刚才的文件/工具/chunk/证据/查询”，优先使用操作记录中的路径、工具参数和检索来源补全。"
                    "只输出改写后的问题，不要解释。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"历史摘要：\n{memory.summary or '无'}\n\n"
                    f"最近5轮详细对话：\n{history or '无'}\n\n"
                    f"最近3轮操作记录：\n{operations or '无'}\n\n"
                    f"当前问题：{question}"
                ),
            },
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


def _format_operations(memory: ConversationMemory) -> str:
    lines = []
    for index, operation in enumerate(memory.operations[-3:], start=1):
        lines.append(
            "\n".join(
                [
                    f"{index}. standalone_question: {operation.standalone_question}",
                    f"   intent: {operation.intent or ''}",
                    f"   selected_tool: {operation.selected_tool or ''}",
                    f"   tool_args: {operation.tool_args}",
                    f"   queries: {operation.queries}",
                    f"   evidence_sources: {operation.evidence_sources}",
                    f"   retrieval: grade={operation.retrieval_grade or ''}, score={operation.retrieval_score}",
                    f"   self_check: {operation.self_check_result}",
                ]
            )
        )
    return "\n".join(lines)


def _format_artifacts(memory: ConversationMemory) -> str:
    return "\n".join(
        f"- name={artifact.name}; type={artifact.artifact_type}; path={artifact.file_path}; "
        f"summary={artifact.summary}; updated_at={artifact.updated_at}"
        for artifact in memory.artifacts[:10]
    )


def _format_summary(value: dict, fallback: str) -> str:
    if value:
        return json.dumps(value, ensure_ascii=False, default=str)
    return fallback or "{}"


def _format_recent_messages(memory: ConversationMemory) -> str:
    return "\n".join(
        f"{message.role}: {_clip_context(message.content, 1200)}"
        for message in memory.messages[-6:]
    ) or "none"


def _format_events(events: list[dict]) -> str:
    if not events:
        return "none"
    return "\n".join(
        f"- type={event.get('event_type', '')}; importance={event.get('importance', 0)}; "
        f"score={event.get('memory_score', '')}; content={event.get('content', '')}"
        for event in events[:10]
    )


def _clip_context(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[:maximum] + "..."
