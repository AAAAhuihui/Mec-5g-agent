"""LLM-based extraction of high-value, durable conversation events."""

from __future__ import annotations

from typing import Any

from app.agent.memory_repositories import EventRepository
from app.llm.deepseek_client import DeepSeekClient


EVENT_TYPES = {
    "GOAL_CHANGE", "TASK_DECISION", "FILE_CREATED", "FILE_MODIFIED", "TOOL_FAILURE",
    "TOOL_SUCCESS", "IMPORTANT_FACT", "USER_PREFERENCE", "ENVIRONMENT_CHANGE",
}


class EventExtractor:
    def __init__(self, repository: EventRepository | None = None, client: DeepSeekClient | None = None) -> None:
        self._repository = repository or EventRepository()
        self._client = client or DeepSeekClient()

    def extract_and_store(
        self,
        session_id: str,
        *,
        user_message: str,
        assistant_response: str,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        result = self._extract(user_message, assistant_response, tool_results or [])
        if not result or not result["save"]:
            return None
        self._repository.create_event(
            session_id,
            event_type=result["event_type"],
            content=result["content"],
            importance=result["importance"],
            metadata={"source": "event_extractor"},
        )
        self._repository.delete_old_events(session_id)
        return result

    def _extract(self, user_message: str, assistant_response: str, tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
        data = self._client.json_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是 Agent Memory Event Extractor。判断交互是否产生值得长期保存的重要事件。"
                        "可保存：用户目标变化、文件创建或修改、工具失败/成功、关键技术决策、环境变化、用户明确偏好。"
                        "不要保存普通聊天、简单解释或临时信息。只输出 JSON："
                        '{"save":true/false,"event_type":"","content":"","importance":0-1}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"用户消息：\n{user_message}\n\n助手回答：\n{assistant_response}\n\n"
                        f"工具结果：\n{tool_results}"
                    ),
                },
            ]
        )
        return _validate_event(data)


def _validate_event(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("save"), bool):
        return None
    if not value["save"]:
        return {"save": False, "event_type": "", "content": "", "importance": 0.0}
    event_type = str(value.get("event_type", "")).strip().upper()
    content = str(value.get("content", "")).strip()
    try:
        importance = float(value.get("importance", 0.0))
    except (TypeError, ValueError):
        return None
    if event_type not in EVENT_TYPES or not content or not 0.0 <= importance <= 1.0:
        return None
    return {"save": True, "event_type": event_type, "content": content[:2000], "importance": importance}
