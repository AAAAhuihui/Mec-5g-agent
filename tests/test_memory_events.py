from __future__ import annotations

from app.agent.contextualizer import build_memory_context
from app.agent.event_extractor import EventExtractor
from app.agent.memory import ConversationMemory, Message, _llm_update_split_summaries


class _SummaryClient:
    available = True

    def json_chat(self, messages):
        assert "recent_messages" in messages[-1]["content"]
        assert "current_react_task_state" in messages[-1]["content"]
        return {
            "semantic_summary": {
                "user_preferences": {"answer_style": "detailed"},
                "environment": {"os": "Windows"},
            },
            "task_summary": {
                "current_goal": "Optimize Agent Memory",
                "completed": ["MySQL Memory"],
                "next_steps": ["Add Memory Planner"],
            },
        }


def test_split_summary_separates_stable_and_task_state() -> None:
    result = _llm_update_split_summaries(
        {},
        {},
        [Message(role="user", content="Please make answers detailed and optimize Agent Memory")],
        {"status": "running", "question": "Optimize Agent Memory"},
        client=_SummaryClient(),
    )

    assert result is not None
    semantic, task = result
    assert semantic["user_preferences"]["answer_style"] == "detailed"
    assert semantic["environment"]["os"] == "Windows"
    assert task["current_goal"] == "Optimize Agent Memory"
    assert task["next_steps"] == ["Add Memory Planner"]


class _EventClient:
    def json_chat(self, messages):
        assert "docker build" in messages[-1]["content"]
        return {
            "save": True,
            "event_type": "TOOL_FAILURE",
            "content": "docker build failed: permission denied",
            "importance": 0.95,
        }


class _EventRepository:
    def __init__(self) -> None:
        self.events = []
        self.pruned = False

    def create_event(self, session_id, **event) -> None:
        self.events.append({"session_id": session_id, **event})

    def delete_old_events(self, session_id, keep=200) -> int:
        self.pruned = True
        return 0


def test_event_extractor_records_docker_build_failure() -> None:
    repository = _EventRepository()
    result = EventExtractor(repository=repository, client=_EventClient()).extract_and_store(
        "session-1",
        user_message="Build the image",
        assistant_response="The build failed.",
        tool_results=[{"command": "docker build .", "stderr": "permission denied"}],
    )

    assert result is not None
    assert repository.events[0]["event_type"] == "TOOL_FAILURE"
    assert repository.events[0]["importance"] == 0.95
    assert repository.pruned is True


class _ImportantEvents:
    def get_important_events(self, session_id, *, query, limit, min_importance):
        assert session_id == "session-1"
        assert query == "Continue the memory work"
        assert limit == 10
        assert min_importance == 0.7
        return [
            {
                "event_type": "TOOL_FAILURE",
                "content": "docker build failed: permission denied",
                "importance": 0.95,
                "memory_score": 0.9,
            }
        ]


def test_context_builder_contains_split_summaries_messages_and_events() -> None:
    memory = ConversationMemory(
        conversation_id="session-1",
        semantic_summary={"environment": {"os": "Windows"}},
        task_summary={"current_goal": "Optimize Memory"},
        messages=[Message(role="user", content="Continue the memory work")],
    )

    context = build_memory_context(memory, "Continue the memory work", _ImportantEvents())

    assert "semantic_summary:" in context
    assert "task_summary:" in context
    assert "recent_messages:" in context
    assert "important_events:" in context
    assert "Optimize Memory" in context
    assert "docker build failed" in context
