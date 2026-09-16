from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.agent.state import RAGState, initial_state
from app.agent.task_planner import create_task_plan
from app.agent.task_timing import (
    ensure_task_timing,
    finalize_task_timing,
    pause_task_timing,
    resume_task_timing,
    start_task_timing,
)
from app.agent.workflow import RAGWorkflow
from app.config import settings
from app.tools.cmd_approvals import (
    CmdApprovalError,
    is_read_only_cmd,
    request_cmd_approval,
    run_read_only_cmd,
)
from app.tools.command_guard import (
    build_command_record,
    detect_command_loop,
    evaluate_command_progress,
    normalize_command,
    redact_command,
)
from app.tools.file_tools import FileToolError, find_files, read_file, request_file_delete_approval, request_file_write_approval


logger = logging.getLogger(__name__)


class ReActWorkflow:
    """Bounded tool-use loop with CMD approval, loop detection, and reflection."""

    def __init__(self, helper: RAGWorkflow | None = None) -> None:
        self._helper = helper or RAGWorkflow()

    def run(self, question: str, *, answer_only: bool = False) -> RAGState:
        state = initial_state(question)
        state["task_id"] = uuid4().hex
        start_task_timing(state)
        self._helper._classify(state)
        state["task_plan"] = create_task_plan(question)
        # Detail-only follow-ups still use the same ReAct finish_task exit so
        # no final answer path can discard prior tool observations.
        return self._drive(state)

    def resume(self, state: RAGState, cmd_result: dict[str, Any]) -> RAGState:
        """Resume the same ReAct task after one approved/rejected CMD result."""
        self._ensure_react_fields(state)
        resume_task_timing(state)
        pending_approval = dict(state.get("approval") or {})
        approval_type = str(pending_approval.get("approval_type", "cmd_execute"))
        command = str(pending_approval.get("command", ""))
        cwd = str(pending_approval.get("working_dir", settings.cmd_tool_allowed_workdir))
        state["approval"] = {}
        state["task_status"] = "running"
        observation = self._command_observation(cmd_result) if approval_type == "cmd_execute" else dict(cmd_result)
        self._append_step(
            state,
            approval_type,
            {"approved": cmd_result.get("status") != "rejected", "command": command, "path": pending_approval.get("path", "")},
            observation,
        )
        if cmd_result.get("status") != "rejected":
            self._mark_tool_execution(state)
        self._update_plan(
            state,
            approval_type,
            "rejected" if cmd_result.get("status") == "rejected" else "completed",
        )
        if command:
            progress = self._record_command_result(state, command, cwd, observation)
            if not progress["has_progress"] and state["no_progress_count"] >= settings.react_max_no_progress_steps:
                return self._finish_blocked(state, "连续命令执行没有获得新信息。")
        return self._drive(state)

    def _drive(self, state: RAGState) -> RAGState:
        self._ensure_react_fields(state)
        while self._within_budget(state):
            decision = self._helper._llm_select_tool(state["question"], state)
            if decision is None:
                return self._finish_blocked(state, "模型未返回合法的下一步工具决策。")
            decision = self._apply_local_project_guard(state, decision)
            tool = str(decision["tool"])
            args = dict(decision.get("args", {}))
            state["selected_tool"] = tool
            state["tool_args"] = args
            state["tool_selection_source"] = str(decision.get("source", "react"))
            state["tool_selection_reason"] = str(decision.get("reason", ""))
            state["last_tool_call_id"] = str(decision.get("tool_call_id", ""))

            if tool == "finish_task":
                state["draft_answer"] = str(args["answer"])
                state["final_answer"] = state["draft_answer"]
                state["task_status"] = "completed"
                state["task_completed"] = True
                self._update_plan(state, tool, "completed")
                self._finish_plan(state)
                state["self_check_result"] = _final_check()
                return self._finalize_task(state)
            if tool == "cmd_execute":
                command = str(args.get("command", ""))
                if detect_command_loop(
                    state["command_history"],
                    command,
                    repeat_limit=settings.react_max_same_command_retries,
                ):
                    self._enter_reflection(state, tool, args, command)
                    if state["no_progress_count"] >= settings.react_max_no_progress_steps:
                        return self._finish_blocked(state, "模型持续请求已无进展的相同 CMD 命令。")
                    continue
                if is_read_only_cmd(command):
                    self._mark_tool_execution(state)
                    observation = run_read_only_cmd(args)
                    self._append_step(state, tool, args, observation)
                    progress = self._record_command_result(
                        state,
                        command,
                        str(args.get("working_dir") or settings.cmd_tool_allowed_workdir),
                        observation,
                    )
                    self._update_plan(state, tool, "completed" if observation.get("success") else "failed")
                    if not progress["has_progress"] and state["no_progress_count"] >= settings.react_max_no_progress_steps:
                        return self._finish_blocked(state, "连续命令执行没有获得新信息。")
                    continue
                return self._pause_for_cmd(state, args)
            if tool == "file_write":
                return self._pause_for_file_write(state, args)
            if tool == "file_delete":
                return self._pause_for_file_delete(state, args)

            self._mark_tool_execution(state)
            observation = self._run_read_only_tool(state, tool, args)
            self._append_step(state, tool, args, observation)
            self._update_plan(state, tool, "completed" if observation.get("success") else "failed")

        return self._finish_blocked(state, "达到 ReAct 最大工具步骤或工具调用上限。")

    @staticmethod
    def _apply_local_project_guard(state: RAGState, decision: dict[str, Any]) -> dict[str, Any]:
        """Require evidence before answering an in-scope local project analysis request."""
        project_dir = _local_project_analysis_directory(str(state.get("question", "")))
        if project_dir is None:
            return decision

        steps = list(state.get("react_steps") or [])
        searches = [
            step for step in steps
            if step.get("tool") == "file_search" and bool((step.get("observation") or {}).get("success"))
        ]
        if not searches:
            # If a file search has actually failed, let the model report the
            # concrete observation instead of repeatedly forcing the same call.
            if any(step.get("tool") == "file_search" for step in steps):
                return decision
            return _force_local_file_tool(
                state,
                decision,
                "file_search",
                {"query": project_dir.name or project_dir.parent.name},
                "本地项目分析必须先搜索项目文件。",
            )

        if any(
            step.get("tool") == "file_read" and bool((step.get("observation") or {}).get("success"))
            for step in steps
        ):
            return decision

        candidates = _prioritized_search_files(searches)
        attempted = {
            str((step.get("args") or {}).get("path", "")).casefold()
            for step in steps
            if step.get("tool") == "file_read"
        }
        next_file = next((path for path in candidates if path.casefold() not in attempted), None)
        if next_file:
            return _force_local_file_tool(
                state,
                decision,
                "file_read",
                {"path": next_file},
                "本地项目分析必须读取至少一个实际文件后才能总结。",
            )
        # Search found no readable candidate, or every bounded candidate
        # returned an actual error. The model may now report that observation.
        return decision

    def _run_read_only_tool(
        self, state: RAGState, tool: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        if tool == "code_search":
            self._helper._run_code_analysis(state)
        elif tool == "web_search":
            self._helper._run_web_search(state)
        elif tool == "analyze_pod_bug":
            self._helper._run_pod_diagnostics(state)
        elif tool == "rag_retrieve":
            self._run_rag(state)
        elif tool == "file_read":
            try:
                return read_file(args)
            except FileToolError as exc:
                return {"success": False, "error": str(exc), "tool": tool}
        elif tool == "file_search":
            try:
                return find_files(args)
            except FileToolError as exc:
                return {"success": False, "error": str(exc), "tool": tool}
        else:
            return {"success": False, "error": f"不支持的只读工具：{tool}"}

        return {
            "success": bool(state.get("evidence_facts") or state.get("reranked_docs")),
            "retrieval_grade": state.get("retrieval_grade"),
            "facts": [
                {"claim": _clip(str(item.get("claim", "")), 500), "source": item.get("source", "")}
                for item in state.get("evidence_facts", [])[:6]
            ],
            "tool_answer": _clip(str(state.get("draft_answer", "")), 1000),
        }

    def _run_rag(self, state: RAGState) -> None:
        self._helper._rewrite_queries(state)
        while True:
            self._helper._retrieve(state)
            if self._helper._route_after_retrieve(state) != "retrieve":
                break
        self._helper._compress_evidence(state)

    def _pause_for_cmd(self, state: RAGState, args: dict[str, Any]) -> RAGState:
        if state["cmd_approval_count"] >= settings.react_max_cmd_approvals:
            return self._finish_blocked(state, "达到 CMD 审批次数上限，未再创建命令。")
        try:
            approval = request_cmd_approval(args)
        except CmdApprovalError as exc:
            observation = {"success": False, "error": str(exc)}
            self._append_step(state, "cmd_execute", args, observation)
            return self._finish_blocked(state, f"CMD 审批创建失败：{exc}")
        state["cmd_approval_count"] += 1
        self._update_plan(state, "cmd_execute", "waiting_approval")
        state["approval"] = approval
        state["task_status"] = "needs_approval"
        pause_task_timing(state)
        state["draft_answer"] = "已生成待审批 CMD 命令；请输入 y 执行，或输入 n/直接回车拒绝。"
        state["final_answer"] = state["draft_answer"]
        state["self_check_result"] = _final_check()
        return state

    def _pause_for_file_write(self, state: RAGState, args: dict[str, Any]) -> RAGState:
        if state["file_approval_count"] >= settings.react_max_file_approvals:
            return self._finish_blocked(state, "达到文件写入审批次数上限。")
        try:
            approval = request_file_write_approval(args)
        except FileToolError as exc:
            self._append_step(state, "file_write", args, {"success": False, "error": str(exc)})
            return self._finish_blocked(state, f"文件写入审批创建失败：{exc}")
        state["file_approval_count"] += 1
        self._update_plan(state, "file_write", "waiting_approval")
        state["approval"] = approval
        state["task_status"] = "needs_approval"
        pause_task_timing(state)
        state["draft_answer"] = "已生成待审批文件写入；请输入 y 写入，或输入 n/直接回车拒绝。"
        state["final_answer"] = state["draft_answer"]
        state["self_check_result"] = _final_check()
        return state

    def _pause_for_file_delete(self, state: RAGState, args: dict[str, Any]) -> RAGState:
        if state["file_approval_count"] >= settings.react_max_file_approvals:
            return self._finish_blocked(state, "达到文件删除审批次数上限。")
        try:
            approval = request_file_delete_approval(args)
        except FileToolError as exc:
            self._append_step(state, "file_delete", args, {"success": False, "error": str(exc)})
            return self._finish_blocked(state, f"文件删除审批创建失败：{exc}")
        state["file_approval_count"] += 1
        self._update_plan(state, "file_delete", "waiting_approval")
        state["approval"] = approval
        state["task_status"] = "needs_approval"
        pause_task_timing(state)
        state["draft_answer"] = "已生成待审批文件删除；请输入 y 删除，或输入 n/直接回车拒绝。"
        state["final_answer"] = state["draft_answer"]
        state["self_check_result"] = _final_check()
        return state

    def _enter_reflection(
        self, state: RAGState, tool: str, args: dict[str, Any], command: str
    ) -> None:
        normalized = normalize_command(command)
        matching = [
            item for item in state["command_history"] if item.get("normalized_command") == normalized
        ]
        latest = dict(matching[-1]) if matching else {}
        state["loop_detected"] = True
        state["no_progress_count"] += 1
        state["reflection_context"] = {
            "task_goal": state["question"],
            "command": command,
            "normalized_command": normalized,
            "repeat_count": len(matching),
            "exit_code": latest.get("exit_code"),
            "stdout": _clip(str(latest.get("stdout", "")), 1200),
            "stderr": _clip(str(latest.get("stderr", "")), 1200),
            "reason": "该命令最近两次产生相同结果，继续执行不会获得新信息。",
            "prohibited_commands": [item.get("command") for item in matching[-settings.react_max_same_command_retries :]],
            "allowed_next_actions": ["不同的诊断命令", "请求缺失信息", "finish_task", "报告阻塞原因"],
        }
        self._log_event(state, tool, command, latest, has_progress=False, next_node="reflection")
        observation = {
            "success": False,
            "guard_action": "reflection",
            "loop_detected": True,
            "has_progress": False,
            "no_progress_count": state["no_progress_count"],
            "reason": state["reflection_context"]["reason"],
            "prohibited_command": command,
            "previous_result": latest,
        }
        self._append_step(state, tool, args, observation)

    def _record_command_result(
        self, state: RAGState, command: str, cwd: str, observation: dict[str, Any]
    ) -> dict[str, Any]:
        record = build_command_record(command, cwd, observation)
        state["command_history"].append(record)
        progress = evaluate_command_progress(state["command_history"], record)
        record["progress"] = progress
        if progress["has_progress"]:
            state["no_progress_count"] = 0
            state["loop_detected"] = False
            state["reflection_context"] = {}
        else:
            state["no_progress_count"] += 1
            state["reflection_context"] = {
                "task_goal": state["question"],
                "command": command,
                "reason": progress["reason"],
                "recommended_next_action": progress["recommended_next_action"],
            }
        self._log_event(
            state,
            "cmd_execute",
            command,
            record,
            has_progress=bool(progress["has_progress"]),
            next_node="agent" if progress["has_progress"] else "reflection",
        )
        return progress

    def _within_budget(self, state: RAGState) -> bool:
        return (
            state["step_count"] < settings.react_max_steps
            and state["tool_call_count"] < settings.react_max_tool_calls
        )

    def _mark_tool_execution(self, state: RAGState) -> None:
        state["iteration_count"] += 1
        state["tool_call_count"] += 1
        state["step_count"] += 1

    def _finish_blocked(self, state: RAGState, reason: str) -> RAGState:
        summaries = [
            {
                "command": item.get("command"),
                "exit_code": item.get("exit_code"),
                "success": item.get("success"),
                "stdout": _clip(str(item.get("stdout", "")), 300),
                "stderr": _clip(str(item.get("stderr", "")), 300),
            }
            for item in state.get("command_history", [])[-3:]
        ]
        state["draft_answer"] = (
            f"任务已阻塞：{reason}\n"
            f"已执行命令及结果摘要：{json.dumps(summaries, ensure_ascii=False)}\n"
            "请基于上述结果提供不同的诊断方向，或补充缺失的环境/权限/配置条件。"
        )
        state["final_answer"] = state["draft_answer"]
        state["task_status"] = "blocked"
        state["task_completed"] = False
        self._finish_plan(state)
        state["self_check_result"] = _final_check(reason)
        self._log_event(state, "blocked_report", "", {}, has_progress=False, next_node="final")
        return self._finalize_task(state)

    @staticmethod
    def _finalize_task(state: RAGState) -> RAGState:
        finalize_task_timing(state)
        return state

    @staticmethod
    def _log_event(
        state: RAGState,
        tool_name: str,
        command: str,
        result: dict[str, Any],
        *,
        has_progress: bool,
        next_node: str,
    ) -> None:
        logger.info(
            "react_event=%s",
            json.dumps(
                {
                    "step_count": state.get("step_count", 0),
                    "tool_name": tool_name,
                    "command": redact_command(command),
                    "normalized_command": result.get("normalized_command", normalize_command(command)),
                    "purpose": state.get("tool_selection_reason", ""),
                    "exit_code": result.get("exit_code"),
                    "success": result.get("success"),
                    "loop_detected": state.get("loop_detected", False),
                    "has_progress": has_progress,
                    "no_progress_count": state.get("no_progress_count", 0),
                    "next_node": next_node,
                },
                ensure_ascii=False,
            ),
        )

    @staticmethod
    def _command_observation(cmd_result: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": cmd_result.get("status") == "completed" and cmd_result.get("exit_code") == 0,
            "status": cmd_result.get("status"),
            "exit_code": cmd_result.get("exit_code"),
            "stdout": _clip(str(cmd_result.get("stdout", "")), settings.cmd_tool_max_output_chars),
            "stderr": _clip(str(cmd_result.get("stderr", "")), settings.cmd_tool_max_output_chars),
            "error": cmd_result.get("error"),
            "timed_out": cmd_result.get("status") == "timed_out",
        }

    @staticmethod
    def _append_step(
        state: RAGState, tool: str, args: dict[str, Any], observation: dict[str, Any]
    ) -> None:
        state["react_steps"].append(
            {
                "index": len(state["react_steps"]) + 1,
                "tool": tool,
                "args": args,
                "observation": observation,
            }
        )
        tool_call_id = state.get("last_tool_call_id", "")
        messages = state.get("llm_messages", [])
        if tool_call_id and messages:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(observation, ensure_ascii=False, default=str),
                }
            )
            state["last_tool_call_id"] = ""

    @staticmethod
    def _ensure_react_fields(state: RAGState) -> None:
        """Migrate persisted task states created before command history was expanded."""
        state.setdefault("task_plan", [])
        state.setdefault("react_steps", [])
        legacy_history = state.get("command_history", [])
        if isinstance(legacy_history, dict):
            state["command_history"] = [
                build_command_record(
                    str(item.get("command", fingerprint)),
                    str(item.get("cwd", settings.cmd_tool_allowed_workdir)),
                    item,
                )
                for fingerprint, item in legacy_history.items()
            ]
        else:
            state.setdefault("command_history", [])
        state.setdefault("step_count", int(state.get("tool_call_count", 0)))
        state.setdefault("no_progress_count", 0)
        state.setdefault("loop_detected", False)
        state.setdefault("task_completed", False)
        state.setdefault("reflection_context", {})
        state.setdefault("llm_messages", [])
        state.setdefault("last_tool_call_id", "")
        state.setdefault("iteration_count", 0)
        state.setdefault("tool_call_count", 0)
        state.setdefault("cmd_approval_count", 0)
        state.setdefault("file_approval_count", 0)
        ensure_task_timing(state)

    @staticmethod
    def _update_plan(state: RAGState, tool: str, status: str) -> None:
        for item in state.get("task_plan", []):
            if item.get("tool") == tool and item.get("status") in {"pending", "waiting_approval"}:
                item["status"] = status
                return

    @staticmethod
    def _finish_plan(state: RAGState) -> None:
        for item in state.get("task_plan", []):
            if item.get("status") in {"pending", "waiting_approval"}:
                item["status"] = "skipped"


def _local_project_analysis_directory(question: str) -> Path | None:
    markers = (
        "分析", "审查", "解读", "梳理", "代码结构", "项目结构", "代码库", "架构",
        "analyze", "review", "audit", "codebase", "architecture",
    )
    if not any(marker in question.casefold() for marker in markers):
        return None
    root = settings.file_tool_allowed_root.expanduser().resolve()
    normalized = question.replace("/", "\\")
    matches = re.findall(r"[A-Za-z]:\\[^\r\n\"'<>|]*", normalized)
    for match in matches:
        candidate = match.rstrip(" ，。；！？?：:、）)]}】")
        # Natural-language suffixes such as “中的代码结构” can be captured by
        # the path expression. Find the longest existing directory prefix.
        for end in range(len(candidate), 2, -1):
            path = Path(candidate[:end].rstrip(" .，。；！？?：:"))
            try:
                resolved = path.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if resolved.is_dir():
                return resolved
    return None


def _prioritized_search_files(searches: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for search in reversed(searches):
        files = (search.get("observation") or {}).get("files") or []
        for item in files:
            path = str(item.get("path", "")).strip() if isinstance(item, dict) else ""
            if path and path not in paths:
                paths.append(path)

    def priority(path: str) -> tuple[int, str]:
        name = Path(path).name.casefold()
        if name in {"readme.md", "readme.txt"}:
            return 0, name
        if name in {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"}:
            return 1, name
        if name.endswith(".py"):
            return 2, name
        return 3, name

    return sorted(paths, key=priority)[:5]


def _force_local_file_tool(
    state: RAGState,
    decision: dict[str, Any],
    tool: str,
    args: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Replace the pending LLM tool call so tool-call history stays valid."""
    for message in reversed(state.get("llm_messages") or []):
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        if message.get("role") != "assistant" or not isinstance(calls, list) or not calls:
            continue
        function = calls[0].get("function") if isinstance(calls[0], dict) else None
        if isinstance(function, dict):
            function["name"] = tool
            function["arguments"] = json.dumps(args, ensure_ascii=False)
        break
    guarded = dict(decision)
    guarded.update({"tool": tool, "args": args, "source": "local_project_guard", "reason": reason})
    return guarded


def is_answer_elaboration_request(question: str) -> bool:
    normalized = " ".join(str(question).strip().casefold().split())
    markers = ("详细一点", "详细些", "展开一点", "展开说明", "多讲一点", "深入一点", "说详细", "more detail")
    return any(marker in normalized for marker in markers)


def run_react_workflow(question: str, *, answer_only: bool = False) -> RAGState:
    return ReActWorkflow().run(question, answer_only=answer_only)


def resume_react_workflow(state: RAGState, cmd_result: dict[str, Any]) -> RAGState:
    return ReActWorkflow().resume(state, cmd_result)


def _final_check(reason: str = "") -> dict[str, Any]:
    return {
        "faithfulness": "supported",
        "unsupported_claims": [],
        "missing_points": [reason] if reason else [],
        "need_more_retrieval": False,
        "action": "final",
    }


def _clip(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[:maximum] + "..."
