from __future__ import annotations

from app.agent.classifier import classify_question
from app.agent.query_rewriter import generate_queries
from app.agent.state import initial_state
from app.agent.workflow import RAGWorkflow
from app.rag.evidence_compressor import compress_evidence
from app.tools.code_search_tool import extract_code_path, search_code
from app.tools.k8s_diagnostics_tool import run_pod_diagnostics


def test_classifier_marks_domain_question_for_retrieval() -> None:
    result = classify_question("NEF 返回成功但流量没有进入 MEC APP，为什么？")
    assert result["need_retrieval"] is True
    assert result["intent"] == "troubleshooting"


def test_query_rewriter_generates_multiple_queries() -> None:
    queries = generate_queries("NEF 返回成功但流量没有进入 MEC APP", "troubleshooting", ["NEF", "MEC APP"])
    assert len(queries) >= 5
    assert any("UPF" in query for query in queries)


def test_evidence_compressor_extracts_facts() -> None:
    docs = [
        {
            "source": "traffic_influence.md",
            "content": "NEF 返回成功不等于 UPF 已经完成用户面分流规则安装。SMF 需要把 PDR/FAR 安装到 UPF。",
        }
    ]
    facts = compress_evidence(docs)
    assert facts
    assert facts[0]["source"] == "traffic_influence.md"


def test_classifier_routes_code_analysis_to_workflow() -> None:
    result = classify_question("在 app/agent/workflow.py 代码中定位关键词 RAGWorkflow")
    assert result["intent"] == "code_analysis"
    assert result["need_retrieval"] is True


def test_code_search_finds_keyword_in_file() -> None:
    results = search_code("RAGWorkflow", "app/agent/workflow.py", max_results=3)
    assert results[0]["status"] == "matched"
    assert results[0]["line"]


def test_workflow_code_analysis_uses_code_search_tool() -> None:
    workflow = RAGWorkflow()
    workflow._llm_select_tool = lambda question, state: {
        "tool": "code_search",
        "args": {"repo_path": "app/agent/workflow.py", "query": "RAGWorkflow"},
        "reason": "测试中模拟 LLM 选择代码搜索工具。",
        "source": "llm",
    }
    state = workflow.run("在 app/agent/workflow.py 代码中定位关键词 `RAGWorkflow`")
    assert state["intent"] == "code_analysis"
    assert state["selected_tool"] == "code_search"
    assert state["retrieval_grade"] == "correct"
    assert state["evidence_facts"]


def test_workflow_code_analysis_handles_absolute_path_search() -> None:
    workflow = RAGWorkflow()
    workflow._llm_select_tool = lambda question, state: {
        "tool": "code_search",
        "args": {
            "repo_path": r"D:\实习\Agent\mec_5g_rag_agent\app\agent\workflow.py",
            "query": "RAGWorkflow",
        },
        "reason": "测试中模拟 LLM 选择代码搜索工具。",
        "source": "llm",
    }
    question = r"在 D:\实习\Agent\mec_5g_rag_agent\app\agent\workflow.py 中搜索关键词 `RAGWorkflow`"
    state = workflow.run(question)
    assert state["intent"] == "code_analysis"
    assert state["retrieval_grade"] == "correct"
    assert "29522-gh0.pdf" not in {doc["source"] for doc in state["reranked_docs"]}


def test_workflow_code_analysis_ignores_quoted_path_when_extracting_query() -> None:
    workflow = RAGWorkflow()
    workflow._llm_select_tool = lambda question, state: {
        "tool": "code_search",
        "args": {"repo_path": r"D:\HiAI\Hello.py", "query": "print"},
        "reason": "测试中模拟 LLM 选择代码搜索工具。",
        "source": "llm",
    }
    question = r'在"D:\HiAI\Hello.py"中搜索print'
    state = workflow.run(question)
    assert state["intent"] == "code_analysis"
    assert state["queries"] == ["print"]


def test_workflow_code_analysis_stops_path_at_source_extension() -> None:
    question = r"在D:\HiAI\Hello.py中搜索print"
    assert str(extract_code_path(question)) == r"D:\HiAI\Hello.py"


def test_workflow_can_use_llm_selected_code_search_tool(monkeypatch) -> None:
    def fake_llm_select_tool(self, question, state):
        return {
            "tool": "code_search",
            "args": {"repo_path": "app/agent/workflow.py", "query": "RAGWorkflow"},
            "reason": "模拟大模型选择代码搜索工具。",
            "source": "llm",
        }

    monkeypatch.setattr(RAGWorkflow, "_llm_select_tool", fake_llm_select_tool)
    state = RAGWorkflow().run("请帮我定位 RAGWorkflow")
    assert state["selected_tool"] == "code_search"
    assert state["tool_selection_source"] == "llm"
    assert state["queries"] == ["RAGWorkflow"]
    assert state["retrieval_grade"] == "correct"


def test_workflow_uses_finish_task_for_a_simple_answer() -> None:
    workflow = RAGWorkflow()
    workflow._llm_select_tool = lambda question, state: {
        "tool": "finish_task",
        "args": {"answer": "这是直接由 finish_task 返回的答案。"},
        "reason": "简单问题无需额外工具。",
        "source": "test",
    }

    state = workflow.run("解释一个简单概念")

    assert state["selected_tool"] == "finish_task"
    assert state["final_answer"] == "这是直接由 finish_task 返回的答案。"


def test_workflow_normalizes_openai_tool_call() -> None:
    decision = RAGWorkflow()._normalize_tool_call(
        {
            "name": "code_search",
            "arguments": {"repo_path": "app/agent/workflow.py", "query": "RAGWorkflow"},
        }
    )
    assert decision == {
        "tool": "code_search",
        "args": {"repo_path": "app/agent/workflow.py", "query": "RAGWorkflow"},
        "reason": "LLM selected this tool through OpenAI-compatible tool_calls.",
        "source": "openai_tools",
    }


def test_workflow_accepts_llm_selected_web_search() -> None:
    workflow = RAGWorkflow()
    workflow._llm_select_tool = lambda question, state: {
        "tool": "web_search",
        "args": {"query": "最新 3GPP 版本"},
        "reason": "需要时效性外部资料。",
        "source": "llm",
    }
    state = workflow._select_tool(initial_state("3GPP 的最新版本是什么？"))
    assert state["selected_tool"] == "web_search"
    assert state["tool_selection_source"] == "llm"
    assert state["tool_args"] == {"query": "最新 3GPP 版本"}


def test_web_search_results_flow_to_answer_generation(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agent.workflow.run_web_search",
        lambda *args, **kwargs: {
            "query": "3GPP release",
            "docs": [
                {
                    "content": "3GPP 发布了新的 Release 信息。",
                    "source": "https://www.3gpp.org/",
                    "score": 0.9,
                    "metadata": {"tool": "web_search", "title": "3GPP"},
                }
            ],
            "facts": [
                {"claim": "3GPP 发布了新的 Release 信息。", "source": "https://www.3gpp.org/"}
            ],
            "answer": "unused when web evidence exists",
        },
    )
    workflow = RAGWorkflow()
    state = initial_state("3GPP 的最新 Release 是什么？")
    state["tool_args"] = {"query": "3GPP release"}
    workflow._run_web_search(state)

    assert state["evidence_facts"][0]["source"] == "https://www.3gpp.org/"
    assert workflow._route_after_web_search(state) == "generate_answer"


def test_pod_diagnostics_turns_mcp_results_into_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.tools.k8s_diagnostics_tool.call_mcp_tool",
        lambda *args, **kwargs: {
            "pod": {
                "namespace": "mec-prod",
                "name": "order-api-abcde",
                "phase": "Running",
                "conditions": [],
                "containers": [
                    {
                        "name": "app",
                        "ready": False,
                        "restart_count": 3,
                        "state": {"type": "waiting", "reason": "CrashLoopBackOff"},
                    }
                ],
            },
            "events": [
                {"type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container"}
            ],
            "logs": [
                {
                    "container": "app",
                    "stream": "previous",
                    "text": "Error: database connection refused\nnormal log line",
                }
            ],
        },
    )
    result = run_pod_diagnostics(
        {
            "namespace": "mec-prod",
            "pod_name": "order-api-abcde",
            "tail_lines": 100,
        }
    )
    assert result["docs"]
    assert any("connection refused" in fact["claim"] for fact in result["facts"])
    assert any(fact["source"].startswith("k8s://mec-prod/order-api-abcde") for fact in result["facts"])


def test_workflow_normalizes_pod_diagnostics_tool_call() -> None:
    decision = RAGWorkflow()._normalize_tool_call(
        {
            "name": "analyze_pod_bug",
            "arguments": {
                "namespace": "mec-prod",
                "pod_name": "order-api-abcde",
                "container": "app",
                "since_seconds": 1800,
            },
        }
    )
    assert decision is not None
    assert decision["args"] == {
        "namespace": "mec-prod",
        "pod_name": "order-api-abcde",
        "container": "app",
        "since_seconds": 1800,
    }


def test_workflow_stops_when_llm_does_not_select_tool(monkeypatch) -> None:
    monkeypatch.setattr(RAGWorkflow, "_llm_select_tool", lambda self, question, state: None)
    state = RAGWorkflow().run("在D:\\HiAI\\Hello.py中搜索print")
    assert state["selected_tool"] == "tool_selection_failed"
    assert state["tool_selection_source"] == "llm_failed"
    assert state["retrieval_grade"] == "empty"
    assert not state["reranked_docs"]
