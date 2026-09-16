from __future__ import annotations

import json
import warnings
from typing import Any

warnings.filterwarnings(
    "ignore",
    message="The default value of `allowed_objects` will change in a future version.*",
    category=Warning,
)

from langgraph.graph import END, StateGraph

from app.agent.answer_generator import generate_answer
from app.agent.classifier import classify_question
from app.agent.crag_evaluator import crag_evaluate
from app.agent.query_rewriter import generate_queries
from app.agent.self_checker import self_check
from app.agent.state import RAGState, initial_state
from app.config import settings
from app.llm.deepseek_client import DeepSeekClient
from app.rag.evidence_compressor import compress_evidence
from app.rag.reranker import rerank_docs
from app.rag.retriever import HybridRetriever
from app.tools.cmd_approvals import CmdApprovalError, request_cmd_approval
from app.tools.code_search_tool import run_code_search
from app.tools.definitions import AVAILABLE_TOOLS, TOOL_DEFINITIONS
from app.tools.k8s_diagnostics_tool import run_pod_diagnostics
from app.tools.web_search_tool import run_web_search


MAX_RETRIEVAL_RETRY = 2
MAX_ANSWER_REWRITE = 1


class RAGWorkflow:
    def __init__(self, retriever: HybridRetriever | None = None) -> None:
        # 允许测试或后续扩展时注入自定义 retriever；真正需要 RAG 检索时再初始化默认 retriever。
        self._retriever = retriever
        self._graph = self._build_graph()

    def run(self, question: str) -> RAGState:
        # 初始化一次问答流程的状态，LangGraph 后续每个节点都在 state 上累积结果。
        return self._graph.invoke(initial_state(question))

    def _build_graph(self) -> Any:
        graph = StateGraph(RAGState)
        graph.add_node("classify", self._classify)
        graph.add_node("select_tool", self._select_tool)
        graph.add_node("tool_selection_failed", self._tool_selection_failed)
        graph.add_node("code_analysis", self._code_analysis)
        graph.add_node("web_search", self._web_search)
        graph.add_node("cmd_approval", self._cmd_approval)
        graph.add_node("pod_diagnostics", self._pod_diagnostics)
        graph.add_node("rewrite_queries", self._rewrite_queries)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("compress_evidence", self._compress_evidence)
        graph.add_node("generate_answer", self._generate_answer)
        graph.add_node("self_check", self._self_check)
        graph.add_node("revise_answer", self._revise_answer)
        graph.add_node("add_self_check_query", self._add_self_check_query)
        graph.add_node("finish_task", self._finish_task)
        graph.add_node("finalize", self._finalize)

        graph.set_entry_point("classify")
        graph.add_edge("classify", "select_tool")
        graph.add_conditional_edges(
            "select_tool",
            self._route_after_tool_selection,
            {
                "code_search": "code_analysis",
                "web_search": "web_search",
                "cmd_execute": "cmd_approval",
                "analyze_pod_bug": "pod_diagnostics",
                "finish_task": "finish_task",
                "rag_retrieve": "rewrite_queries",
                "tool_selection_failed": "tool_selection_failed",
            },
        )
        graph.add_edge("tool_selection_failed", END)
        graph.add_edge("code_analysis", END)
        graph.add_edge("cmd_approval", END)
        graph.add_conditional_edges(
            "web_search",
            self._route_after_web_search,
            {
                "generate_answer": "generate_answer",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "pod_diagnostics",
            self._route_after_pod_diagnostics,
            {
                "generate_answer": "generate_answer",
                "finalize": "finalize",
            },
        )
        graph.add_edge("rewrite_queries", "retrieve")
        graph.add_conditional_edges(
            "retrieve",
            self._route_after_retrieve,
            {
                "retrieve": "retrieve",
                "compress_evidence": "compress_evidence",
            },
        )
        graph.add_edge("compress_evidence", "generate_answer")
        graph.add_edge("generate_answer", "self_check")
        graph.add_conditional_edges(
            "self_check",
            self._route_after_self_check,
            {
                "revise_answer": "revise_answer",
                "add_self_check_query": "add_self_check_query",
                "finalize": "finalize",
            },
        )
        graph.add_edge("revise_answer", "self_check")
        graph.add_edge("add_self_check_query", "retrieve")
        graph.add_edge("finish_task", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()

    def _classify(self, state: RAGState) -> RAGState:
        # 判断问题意图、领域实体，以及是否需要进入 RAG 检索链路。
        state.update(classify_question(state["question"]))
        return state

    def _select_tool(self, state: RAGState) -> RAGState:
        # 由大模型在本地 RAG、联网、代码搜索和直接回答之间自主路由。
        llm_decision = self._llm_select_tool(state["question"], state)
        if llm_decision is None:
            state["selected_tool"] = "tool_selection_failed"
            state["tool_args"] = {}
            state["tool_selection_source"] = "llm_failed"
            state["tool_selection_reason"] = "LLM 未返回合法 tool_calls，已停止工具调用。"
            return state

        state["selected_tool"] = str(llm_decision["tool"])
        state["tool_args"] = dict(llm_decision.get("args", {}))
        state["tool_selection_source"] = str(llm_decision.get("source", "llm"))
        state["tool_selection_reason"] = str(llm_decision.get("reason", ""))
        return state

    def _route_after_tool_selection(self, state: RAGState) -> str:
        if state["selected_tool"] == "code_search":
            return "code_search"
        if state["selected_tool"] == "web_search":
            return "web_search"
        if state["selected_tool"] == "cmd_execute":
            return "cmd_execute"
        if state["selected_tool"] == "analyze_pod_bug":
            return "analyze_pod_bug"
        if state["selected_tool"] == "finish_task":
            return "finish_task"
        if state["selected_tool"] == "rag_retrieve":
            return "rag_retrieve"
        return "tool_selection_failed"

    def _finish_task(self, state: RAGState) -> RAGState:
        """Accept the model's final answer as the sole completion path."""
        state["draft_answer"] = str(state.get("tool_args", {}).get("answer", ""))
        state["self_check_result"] = {
            "faithfulness": "supported",
            "unsupported_claims": [],
            "missing_points": [],
            "need_more_retrieval": False,
            "action": "final",
        }
        return state

    def _llm_select_tool(self, question: str, state: RAGState) -> dict[str, Any] | None:
        client = DeepSeekClient()
        message_history = state.setdefault("llm_messages", [])
        if not message_history:
            message_history.append({"role": "user", "content": question})
        reflection = state.get("reflection_context", {})
        tool_call = client.tool_call(
            [
                {
                    "role": "system",
                    "content": (
                        "你是 Agent 工具路由器。必须通过 tools/tool_calls 选择且只选择一个工具，"
                        "不要在正文里输出 JSON。优先使用本地 rag_retrieve 回答已有 MEC/5G 知识库"
                        "可以覆盖的稳定知识；仅在问题需要最新、实时、外部资料、官网信息、来源链接，"
                        "或本地知识库预计不足时选择 web_search。简单且无需检索的问题选择 "
                        "finish_task；本地代码定位问题选择 code_search。"
                        "当用户提供 namespace 和 Pod 名称并要求分析 Pod/容器故障、日志、重启、"
                        "CrashLoopBackOff 或 OOMKilled 时选择 analyze_pod_bug。"
                    ),
                },
                {
                    "role": "system",
                    "content": (
                        "Security rule: choose cmd_execute only for an explicit user request to run a Windows CMD "
                        "command. Allowlisted read-only commands may execute and return observations; commands that "
                        "write, change state, use chaining/redirection, or are uncertain require human y/n approval. "
                        "Use CMD syntax, for example mkdir \"%USERPROFILE%\\Desktop\\qdh\"."
                        " For an explicit request to create or modify a local text file, choose file_write instead; "
                        "it requires separate approval. For an explicit request to inspect a local text file, choose file_read."
                        " When the user asks to create a program, web page, game, script, or document on the local computer, "
                        "use file_write first rather than rag_retrieve or code_search."
                        " When the user asks to modify an existing local program/file but did not give an exact path, "
                        "use file_search first, then file_read the selected file, then file_write the complete updated content. "
                        "Do not use rag_retrieve or code_search for that local-file workflow."
                        " Local project analysis policy: when the user gives a directory inside FILE_TOOL_ALLOWED_ROOT and "
                        "asks to analyze, review, explain, audit, or summarize code/project structure, call file_search first, "
                        "then file_read README/dependency files and relevant source files. Do not choose "
                        "finish_task before successful file observations. Do not ask the user to paste code unless file tools "
                        "actually fail."
                    ),
                },
                {
                    "role": "system",
                    "content": (
                        f"This is a bounded ReAct loop. Global task plan: {state.get('task_plan', [])}. "
                        f"Completed observations: {state.get('react_steps', [])[-6:]}. Choose exactly one next tool. "
                        f"Executed CMD history (command and result): {state.get('command_history', [])[-6:]}. "
                        f"Reflection context: {reflection}. "
                        "Inspect previous assistant tool calls and their matching tool results before acting. "
                        "Never execute a command again after it produced the same result twice, unless a dedicated "
                        "polling tool explicitly allows it. A successful exit code alone does not complete the task. "
                        "When reflection reports no progress, choose a materially different diagnostic command, ask "
                        "for missing information, or call finish_task. "
                        "Follow the plan where it remains useful, update course from observations, and call finish_task "
                        "when the user goal is sufficiently answered."
                    ),
                },
                {
                    "role": "system",
                    "content": (
                        f"问题：{question}\n"
                        f"规则分类 intent：{state['intent']}\n"
                        f"领域实体：{state['domain_entities']}\n"
                        f"是否需要知识库检索：{state['need_retrieval']}\n"
                        f"Tavily 联网搜索已配置：{bool(settings.tavily_api_key)}\n"
                        f"K8S MCP 地址：{settings.k8s_mcp_url}\n"
                        f"当前可读取的本地文件根目录：{settings.file_tool_allowed_root.resolve()}"
                    ),
                },
                *message_history,
            ],
            tools=TOOL_DEFINITIONS,
            tool_choice="required",
        )
        if not tool_call:
            return None
        decision = self._normalize_tool_call(tool_call)
        if decision is None:
            return None
        tool_call_id = str(tool_call.get("id") or f"local-call-{len(message_history) + 1}")
        decision["tool_call_id"] = tool_call_id
        message_history.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": decision["tool"],
                            "arguments": json.dumps(decision["args"], ensure_ascii=False),
                        },
                    }
                ],
            }
        )
        return decision

    def _normalize_tool_call(
        self,
        tool_call: dict[str, Any],
    ) -> dict[str, Any] | None:
        tool = str(tool_call.get("name", "")).strip()
        if tool not in AVAILABLE_TOOLS:
            return None

        args = tool_call.get("arguments")
        if not isinstance(args, dict):
            args = {}

        if tool == "code_search":
            repo_path = str(args.get("repo_path", "")).strip()
            query = str(args.get("query", "")).strip()
            if not repo_path or not query:
                return None
            args = {"repo_path": repo_path, "query": query}
        elif tool == "web_search":
            query = str(args.get("query", "")).strip()
            if not query:
                return None
            args = {"query": query}
        elif tool == "finish_task":
            answer = str(args.get("answer", "")).strip()
            if not answer:
                return None
            args = {"answer": answer}
        elif tool == "cmd_execute":
            command = str(args.get("command", "")).strip()
            rationale = str(args.get("rationale", "")).strip()
            working_dir = str(args.get("working_dir", "")).strip()
            if not command or not rationale:
                return None
            args = {"command": command, "rationale": rationale}
            if working_dir:
                args["working_dir"] = working_dir
        elif tool == "file_read":
            path = str(args.get("path", "")).strip()
            if not path:
                return None
            args = {"path": path}
        elif tool == "file_search":
            query = str(args.get("query", "")).strip()
            if not query:
                return None
            args = {"query": query}
        elif tool == "file_write":
            path = str(args.get("path", "")).strip()
            content = str(args.get("content", ""))
            rationale = str(args.get("rationale", "")).strip()
            if not path or not content or not rationale:
                return None
            args = {"path": path, "content": content, "rationale": rationale, "overwrite": bool(args.get("overwrite", False))}
        elif tool == "file_delete":
            path = str(args.get("path", "")).strip()
            rationale = str(args.get("rationale", "")).strip()
            if not path or not rationale:
                return None
            args = {"path": path, "rationale": rationale}
        elif tool == "analyze_pod_bug":
            namespace = str(args.get("namespace", "")).strip()
            pod_name = str(args.get("pod_name", "")).strip()
            if not namespace or not pod_name:
                return None
            normalized = {"namespace": namespace, "pod_name": pod_name}
            for optional_key in ("container", "since_seconds", "tail_lines"):
                if optional_key in args and args[optional_key] not in (None, ""):
                    normalized[optional_key] = args[optional_key]
            args = normalized
        else:
            args = {}

        return {
            "tool": tool,
            "args": args,
            "reason": "LLM selected this tool through OpenAI-compatible tool_calls.",
            "source": "openai_tools",
        }

    def _tool_selection_failed(self, state: RAGState) -> RAGState:
        state["draft_answer"] = (
            "结论：\n"
            "本轮没有执行任何工具，因为大模型没有成功选择可用工具。\n\n"
            "详细说明：\n"
            f"{state['tool_selection_reason']}\n\n"
            "下一步建议：\n"
            "请确认 DeepSeek API Key 可用，并确认模型返回了 OpenAI-compatible tool_calls。"
        )
        state["self_check_result"] = {
            "faithfulness": "unsupported",
            "unsupported_claims": ["工具选择失败，未执行检索或代码搜索。"],
            "missing_points": ["selected_tool"],
            "need_more_retrieval": False,
            "action": "final",
        }
        state["final_answer"] = state["draft_answer"]
        return state

    def _code_analysis(self, state: RAGState) -> RAGState:
        # 代码分析类问题不走 Chroma 文档库，改用本地代码搜索工具定位关键词。
        self._run_code_analysis(state)
        return state

    def _web_search(self, state: RAGState) -> RAGState:
        self._run_web_search(state)
        return state

    def _cmd_approval(self, state: RAGState) -> RAGState:
        # This node creates a pending approval only; it never invokes cmd.exe.
        try:
            approval = request_cmd_approval(state.get("tool_args", {}))
        except CmdApprovalError as exc:
            state["draft_answer"] = f"未创建终端命令审批：{exc}"
            state["self_check_result"] = {
                "faithfulness": "supported",
                "unsupported_claims": [],
                "missing_points": [],
                "need_more_retrieval": False,
                "action": "final",
            }
            state["final_answer"] = state["draft_answer"]
            return state

        state["approval"] = approval
        state["draft_answer"] = (
            "已生成待审批的 CMD 命令，尚未执行。请核对命令、工作目录和用途后，"
            "CLI 将要求输入 y 确认执行；输入 n 或直接回车则拒绝。"
        )
        state["self_check_result"] = {
            "faithfulness": "supported",
            "unsupported_claims": [],
            "missing_points": [],
            "need_more_retrieval": False,
            "action": "final",
        }
        state["final_answer"] = state["draft_answer"]
        return state

    def _pod_diagnostics(self, state: RAGState) -> RAGState:
        self._run_pod_diagnostics(state)
        return state

    def _rewrite_queries(self, state: RAGState) -> RAGState:
        # 将原始问题扩展成多条检索 query，提高召回文档片段的概率。
        state["queries"] = generate_queries(
            state["question"],
            state["intent"],
            state["domain_entities"],
        )
        return state

    def _retrieve(self, state: RAGState) -> RAGState:
        # 向量和 BM25 各自扩大召回，再由 RRF 融合为待重排候选集。
        retriever = self._get_retriever()
        docs = retriever.retrieve(
            state["queries"],
            top_k=settings.rag_fused_candidate_k,
        )
        state["retrieved_docs"] = docs

        # Cross-Encoder（或故障时的轻量降级器）将候选集压缩到最终 Top-K。
        windows = retriever.expand_context_windows(
            docs,
            radius=settings.rag_neighbor_radius,
        )
        state["reranked_docs"] = rerank_docs(
            state["question"],
            windows,
            top_k=settings.top_k,
        )

        # CRAG 评估当前检索结果是否足够支持回答，并给出可能的补充 query。
        evaluation = crag_evaluate(state["question"], state["reranked_docs"])
        state["retrieval_grade"] = evaluation["retrieval_grade"]
        state["retrieval_score"] = float(evaluation.get("score", 0.0))

        if state["retrieval_grade"] not in {"correct", "empty"}:
            state["retrieval_retry_count"] += 1
            for query in evaluation.get("next_queries", []):
                if query and query not in state["queries"]:
                    state["queries"].append(query)
        return state

    def _route_after_retrieve(self, state: RAGState) -> str:
        if state["retrieval_grade"] in {"correct", "empty"}:
            return "compress_evidence"
        if state["retrieval_retry_count"] >= MAX_RETRIEVAL_RETRY:
            return "compress_evidence"
        return "retrieve"

    def _route_after_web_search(self, state: RAGState) -> str:
        # 有网页证据时交给大模型综合回答；失败信息则直接返回，避免生成空泛答案。
        return "generate_answer" if state["evidence_facts"] else "finalize"

    def _route_after_pod_diagnostics(self, state: RAGState) -> str:
        return "generate_answer" if state["evidence_facts"] else "finalize"

    def _compress_evidence(self, state: RAGState) -> RAGState:
        # 从重排后的文档中抽取较短的证据事实，减少最终 prompt 的上下文长度。
        state["evidence_facts"] = compress_evidence(state["reranked_docs"])
        return state

    def _generate_answer(self, state: RAGState) -> RAGState:
        # 基于问题、意图和证据事实生成初稿答案。
        state["draft_answer"] = generate_answer(
            state["question"],
            state["evidence_facts"],
            state["intent"],
        )
        return state

    def _self_check(self, state: RAGState) -> RAGState:
        # 对初稿做后置自检，判断是否被证据支持，是否需要重写或补检索。
        state["self_check_result"] = self_check(
            state["question"],
            state["draft_answer"],
            state["evidence_facts"],
        )
        return state

    def _route_after_self_check(self, state: RAGState) -> str:
        action = state["self_check_result"].get("action")
        if action == "revise_answer" and state["answer_rewrite_count"] < MAX_ANSWER_REWRITE:
            return "revise_answer"
        if action == "retrieve_more" and state["retrieval_retry_count"] < MAX_RETRIEVAL_RETRY:
            return "add_self_check_query"
        return "finalize"

    def _revise_answer(self, state: RAGState) -> RAGState:
        # 自检认为答案需要修正时，只允许有限次数重写，避免无限循环。
        state["answer_rewrite_count"] += 1
        state["draft_answer"] = generate_answer(
            state["question"],
            state["evidence_facts"],
            state["intent"],
            revise=True,
        )
        return state

    def _add_self_check_query(self, state: RAGState) -> RAGState:
        # 自检认为证据不足时，追加补充证据 query，再回到 retrieve 节点。
        query = state["question"] + " 补充证据"
        if query not in state["queries"]:
            state["queries"].append(query)
        state["retrieval_retry_count"] += 1
        return state

    def _finalize(self, state: RAGState) -> RAGState:
        state["final_answer"] = state["draft_answer"]
        return state

    def _get_retriever(self) -> HybridRetriever:
        if self._retriever is None:
            self._retriever = HybridRetriever()
        return self._retriever

    def _run_code_analysis(self, state: RAGState) -> None:
        result = run_code_search(
            state["question"],
            tool_args=state.get("tool_args", {}),
            max_results=settings.top_k,
        )
        state["queries"] = [str(result["query"])]
        state["retrieved_docs"] = result["docs"]
        state["reranked_docs"] = result["docs"]
        state["retrieval_grade"] = "correct" if result["docs"] else "empty"
        state["retrieval_score"] = 1.0 if result["docs"] else 0.0
        state["evidence_facts"] = result["facts"]
        state["draft_answer"] = str(result["answer"])
        state["self_check_result"] = {
            "faithfulness": "supported" if result["docs"] else "unsupported",
            "unsupported_claims": [] if result["docs"] else ["没有在给定代码位置找到关键词命中。"],
            "missing_points": [] if result["docs"] else [str(result["query"])],
            "need_more_retrieval": False,
            "action": "final",
        }
        state["final_answer"] = state["draft_answer"]

    def _run_web_search(self, state: RAGState) -> None:
        result = run_web_search(
            state["question"],
            tool_args=state.get("tool_args", {}),
            max_results=settings.web_search_max_results,
        )
        docs = result["docs"]
        state["queries"] = [str(result["query"])]
        state["retrieved_docs"] = docs
        state["reranked_docs"] = docs
        state["retrieval_grade"] = "correct" if docs else "empty"
        state["retrieval_score"] = max(
            (float(doc.get("score", 0.0)) for doc in docs),
            default=0.0,
        )
        state["evidence_facts"] = result["facts"]
        state["draft_answer"] = str(result["answer"])
        state["self_check_result"] = {
            "faithfulness": "supported" if docs else "unsupported",
            "unsupported_claims": [] if docs else [state["draft_answer"]],
            "missing_points": [] if docs else [str(result["query"])],
            "need_more_retrieval": False,
            "action": "final",
        }
        state["final_answer"] = state["draft_answer"]

    def _run_pod_diagnostics(self, state: RAGState) -> None:
        result = run_pod_diagnostics(state.get("tool_args", {}))
        docs = result["docs"]
        state["queries"] = [str(result["query"])] if result["query"] else []
        state["retrieved_docs"] = docs
        state["reranked_docs"] = docs
        state["retrieval_grade"] = "correct" if docs else "empty"
        state["retrieval_score"] = 1.0 if docs else 0.0
        state["evidence_facts"] = result["facts"]
        state["draft_answer"] = str(result["answer"])
        state["self_check_result"] = {
            "faithfulness": "supported" if docs else "unsupported",
            "unsupported_claims": [] if docs else [state["draft_answer"]],
            "missing_points": [] if docs else [str(result["query"])],
            "need_more_retrieval": False,
            "action": "final",
        }
        state["final_answer"] = state["draft_answer"]


def run_workflow(question: str) -> RAGState:
    return RAGWorkflow().run(question)
