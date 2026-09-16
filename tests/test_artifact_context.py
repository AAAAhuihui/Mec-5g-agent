from __future__ import annotations

from app.agent.contextualizer import build_detail_followup_question, build_standalone_question
from app.agent.memory import ArtifactRecord, ConversationMemory, Message


def test_follow_up_includes_durable_session_artifact_when_llm_unavailable(monkeypatch) -> None:
    class _Client:
        available = False

        def chat(self, *args, **kwargs):
            return None

    monkeypatch.setattr("app.agent.contextualizer.DeepSeekClient", _Client)
    memory = ConversationMemory(
        conversation_id="session-1",
        artifacts=[
            ArtifactRecord(
                name="tetris",
                file_path=r"C:\Users\13695\Desktop\tetris.py",
                summary="Pygame 俄罗斯方块游戏",
            )
        ],
    )

    question = build_standalone_question("给这个俄罗斯方块增加变化方块功能", memory)

    assert r"C:\Users\13695\Desktop\tetris.py" in question
    assert "俄罗斯方块" in question


def test_detail_follow_up_uses_previous_valid_answer_not_default_domain() -> None:
    memory = ConversationMemory(
        conversation_id="session-2",
        messages=[
            Message(role="user", content="Agent 的记忆系统主流怎样设计？"),
            Message(role="assistant", content="常见做法是短期上下文、摘要、结构化事实和向量记忆分层。"),
        ],
    )

    question = build_detail_followup_question("回答得详细一点", memory)

    assert "Agent 的记忆系统主流怎样设计" in question
    assert "短期上下文、摘要、结构化事实和向量记忆分层" in question
    assert "不要改换为无关的 MEC/5G" in question
