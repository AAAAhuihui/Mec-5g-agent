from __future__ import annotations

from types import SimpleNamespace

import app.agent.react_workflow as react_workflow_module
from app.agent.react_workflow import ReActWorkflow, is_answer_elaboration_request
from app.tools.file_tools import FileToolError


class _Helper:
    def __init__(self, decisions):
        self.decisions = iter(decisions)

    def _classify(self, state):
        state.update({"intent": "troubleshooting", "domain_entities": [], "need_retrieval": False})
        return state

    def _llm_select_tool(self, question, state):
        return next(self.decisions)

    def _run_code_analysis(self, state):
        state["retrieval_grade"] = "correct"
        state["evidence_facts"] = [{"claim": "命中代码行", "source": "app.py:1"}]
        state["draft_answer"] = "代码搜索完成"

    def _run_web_search(self, state):
        raise AssertionError("not expected")

    def _run_pod_diagnostics(self, state):
        raise AssertionError("not expected")

    def _rewrite_queries(self, state):
        state["queries"] = [state["question"]]

    def _retrieve(self, state):
        state["retrieval_grade"] = "correct"

    def _route_after_retrieve(self, state):
        return "compress_evidence"

    def _compress_evidence(self, state):
        state["evidence_facts"] = [{"claim": "RAG 证据", "source": "doc.md"}]

    def _answer_general(self, question):
        return "兜底回答"


def _decision(tool, args):
    return {"tool": tool, "args": args, "source": "test", "reason": "test"}


def test_react_loops_from_read_only_observation_to_finish() -> None:
    helper = _Helper(
        [
            _decision("code_search", {"repo_path": "app.py", "query": "error"}),
            _decision("finish_task", {"answer": "已根据代码搜索结果完成分析。"}),
        ]
    )
    state = ReActWorkflow(helper).run("定位错误")

    assert state["task_status"] == "completed"
    assert state["final_answer"] == "已根据代码搜索结果完成分析。"
    assert state["react_steps"][0]["tool"] == "code_search"
    assert state["react_steps"][0]["observation"]["facts"]


def test_react_creates_and_updates_global_task_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agent.react_workflow.create_task_plan",
        lambda question: [
            {"index": 1, "goal": "检索代码", "tool": "code_search", "status": "pending"},
            {"index": 2, "goal": "输出结论", "tool": "finish_task", "status": "pending"},
        ],
    )
    helper = _Helper(
        [
            _decision("code_search", {"repo_path": "app.py", "query": "error"}),
            _decision("finish_task", {"answer": "完成。"}),
        ]
    )
    state = ReActWorkflow(helper).run("定位错误")
    assert state["task_plan"][0]["status"] == "completed"
    assert state["task_plan"][1]["status"] == "completed"


def test_react_cmd_pause_then_resume(monkeypatch) -> None:
    helper = _Helper(
        [
            _decision("cmd_execute", {"command": "mkdir qdh", "rationale": "创建目录"}),
            _decision("finish_task", {"answer": "CMD 结果已分析。"}),
        ]
    )
    monkeypatch.setattr(
        "app.agent.react_workflow.request_cmd_approval",
        lambda args: {
            "approval_id": "approval-1", "status": "pending", "command": args["command"],
            "rationale": args["rationale"], "working_dir": "C:\\safe", "created_at": "", "expires_at": "",
        },
    )
    workflow = ReActWorkflow(helper)
    paused = workflow.run("执行 dir 后说明结果")
    assert paused["task_status"] == "needs_approval"

    resumed = workflow.resume(
        paused,
        {"status": "completed", "exit_code": 0, "stdout": "file.txt", "stderr": "", "error": None},
    )
    assert resumed["task_status"] == "completed"
    assert resumed["final_answer"] == "CMD 结果已分析。"
    assert resumed["react_steps"][0]["tool"] == "cmd_execute"


def test_react_runs_allowlisted_read_only_cmd_without_approval(monkeypatch) -> None:
    helper = _Helper(
        [
            _decision("cmd_execute", {"command": "dir", "rationale": "列出目录"}),
            _decision("finish_task", {"answer": "目录已读取。"}),
        ]
    )
    monkeypatch.setattr(
        "app.agent.react_workflow.run_read_only_cmd",
        lambda args: {"success": True, "status": "completed", "exit_code": 0, "stdout": "a.txt"},
    )
    state = ReActWorkflow(helper).run("执行 dir 并说明结果")
    assert state["task_status"] == "completed"
    assert state["approval"] == {}
    assert state["react_steps"][0]["observation"]["stdout"] == "a.txt"


def test_react_blocks_a_repeated_cmd_and_uses_its_previous_result(monkeypatch) -> None:
    helper = _Helper(
        [
            _decision("cmd_execute", {"command": "mysql --version", "rationale": "获取版本"}),
            _decision("cmd_execute", {"command": "  MYSQL   --version  ", "rationale": "重复获取版本"}),
            _decision("finish_task", {"answer": "MySQL 客户端版本已获取。"}),
        ]
    )
    calls: list[dict] = []

    def fake_run(args):
        calls.append(args)
        return {"success": True, "status": "completed", "exit_code": 0, "stdout": "mysql  Ver 14.14"}

    monkeypatch.setattr("app.agent.react_workflow.is_read_only_cmd", lambda command: True)
    monkeypatch.setattr("app.agent.react_workflow.run_read_only_cmd", fake_run)

    state = ReActWorkflow(helper).run("查询本机 MySQL 版本")

    assert state["task_status"] == "completed"
    assert len(calls) == 2
    assert state["command_history"][-1]["stdout"] == "mysql  Ver 14.14"
    assert state["command_history"][-1]["normalized_command"] == "mysql --version"


def test_react_resume_handles_task_state_saved_before_command_history() -> None:
    helper = _Helper([_decision("finish_task", {"answer": "已读取命令结果。"})])
    state = {
        "question": "查询版本",
        "approval": {"command": "mysql --version"},
        "task_status": "needs_approval",
        "react_steps": [],
        "task_plan": [],
        "iteration_count": 0,
        "tool_call_count": 0,
        "cmd_approval_count": 1,
        "evidence_facts": [],
    }

    resumed = ReActWorkflow(helper).resume(
        state, {"status": "completed", "exit_code": 0, "stdout": "mysql 5.7", "stderr": "", "error": None}
    )

    assert resumed["command_history"][-1]["stdout"] == "mysql 5.7"


def test_react_does_not_create_a_second_approval_for_completed_command(monkeypatch) -> None:
    helper = _Helper(
        [
            _decision("cmd_execute", {"command": "mkdir qdh", "rationale": "创建目录"}),
            _decision("cmd_execute", {"command": "mkdir qdh", "rationale": "重复创建目录"}),
            _decision("finish_task", {"answer": "目录创建结果已确认。"}),
        ]
    )
    approvals: list[dict] = []

    def fake_approval(args):
        approvals.append(args)
        return {
            "approval_id": "approval-1", "status": "pending", "command": args["command"],
            "rationale": args["rationale"], "working_dir": "C:\\safe", "created_at": "", "expires_at": "",
        }

    monkeypatch.setattr("app.agent.react_workflow.request_cmd_approval", fake_approval)
    workflow = ReActWorkflow(helper)
    paused = workflow.run("在桌面创建目录")
    resumed = workflow.resume(
        paused, {"status": "completed", "exit_code": 0, "stdout": "", "stderr": "", "error": None}
    )

    assert resumed["task_status"] == "needs_approval"
    assert len(approvals) == 2


def test_third_identical_command_enters_reflection_without_execution(monkeypatch) -> None:
    helper = _Helper(
        [
            _decision("cmd_execute", {"command": "dir", "rationale": "第一次读取"}),
            _decision("cmd_execute", {"command": "  DIR  ", "rationale": "第二次读取"}),
            _decision("cmd_execute", {"command": "dir", "rationale": "第三次读取"}),
            _decision("finish_task", {"answer": "已停止重复查询并汇总现有结果。"}),
        ]
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.agent.react_workflow.run_read_only_cmd",
        lambda args: calls.append(args)
        or {"success": True, "status": "completed", "exit_code": 0, "stdout": "same"},
    )

    state = ReActWorkflow(helper).run("查询目录")

    assert state["task_status"] == "completed"
    assert len(calls) == 2
    reflection = state["react_steps"][2]["observation"]
    assert reflection["guard_action"] == "reflection"
    assert reflection["loop_detected"] is True


def test_react_file_write_pauses_then_resumes(monkeypatch) -> None:
    helper = _Helper(
        [
            _decision("file_write", {"path": "tetris/index.html", "content": "<h1>Tetris</h1>", "rationale": "创建页面", "overwrite": False}),
            _decision("finish_task", {"answer": "游戏文件已写入。"}),
        ]
    )
    monkeypatch.setattr(
        "app.agent.react_workflow.request_file_write_approval",
        lambda args: {"approval_id": "file-1", "approval_type": "file_write", "status": "pending", "path": args["path"], "content": args["content"], "rationale": args["rationale"], "expires_at": ""},
    )
    workflow = ReActWorkflow(helper)
    paused = workflow.run("在桌面写俄罗斯方块")
    assert paused["task_status"] == "needs_approval"
    assert paused["approval"]["approval_type"] == "file_write"

    resumed = workflow.resume(paused, {"status": "completed", "success": True, "path": "tetris/index.html"})
    assert resumed["task_status"] == "completed"
    assert resumed["final_answer"] == "游戏文件已写入。"


def test_file_read_path_error_becomes_observation_instead_of_crashing(monkeypatch) -> None:
    helper = _Helper(
        [
            _decision("file_read", {"path": r"D:\实习\MNIST\README.md"}),
            _decision("finish_task", {"answer": "已说明文件访问范围。"}),
        ]
    )
    monkeypatch.setattr(
        "app.agent.react_workflow.read_file",
        lambda args: (_ for _ in ()).throw(FileToolError("文件路径必须位于允许目录内：C:\\Users\\13695\\Desktop")),
    )

    state = ReActWorkflow(helper).run("读取 MNIST README")

    assert state["task_status"] == "completed"
    assert state["react_steps"][0]["tool"] == "file_read"
    assert state["react_steps"][0]["observation"]["success"] is False
    assert "文件路径必须位于允许目录内" in state["react_steps"][0]["observation"]["error"]


def test_local_project_analysis_guard_forces_search_then_read(monkeypatch, tmp_path) -> None:
    root = tmp_path / "MNIST"
    project = root / "mnist-pytorch"
    project.mkdir(parents=True)
    readme = project / "README.md"
    readme.write_text("# MNIST", encoding="utf-8")
    monkeypatch.setattr(
        react_workflow_module,
        "settings",
        SimpleNamespace(file_tool_allowed_root=root),
    )
    state = {
        "question": f"帮我分析 {project} 中的代码结构，并给出建议",
        "react_steps": [],
        "llm_messages": [],
    }
    original = _decision("finish_task", {"answer": "我无法读取本地文件"})

    search = ReActWorkflow._apply_local_project_guard(state, original)
    assert search["tool"] == "file_search"
    assert search["args"] == {"query": "mnist-pytorch"}

    state["react_steps"].append(
        {"tool": "file_search", "args": search["args"], "observation": {"success": True, "files": [{"path": str(readme)}]}}
    )
    read = ReActWorkflow._apply_local_project_guard(state, original)
    assert read["tool"] == "file_read"
    assert read["args"] == {"path": str(readme)}

    state["react_steps"].append(
        {"tool": "file_read", "args": read["args"], "observation": {"success": True, "content": "# MNIST"}}
    )
    assert ReActWorkflow._apply_local_project_guard(state, original)["tool"] == "finish_task"


def test_detail_follow_up_bypasses_tool_loop() -> None:
    helper = _Helper([_decision("finish_task", {"answer": "兜底回答"})])
    state = ReActWorkflow(helper).run("请把刚才的回答详细一点", answer_only=True)

    assert state["task_status"] == "completed"
    assert state["selected_tool"] == "finish_task"
    assert state["tool_selection_source"] == "test"
    assert state["final_answer"] == "兜底回答"


def test_detail_follow_up_marker_detection() -> None:
    assert is_answer_elaboration_request("回答得详细一点") is True
    assert is_answer_elaboration_request("请创建一个文件") is False
