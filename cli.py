from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient

from app.main import app
from app.rag.document_loader import load_documents
from app.rag.text_splitter import split_documents
from app.rag.vector_store import VectorStore


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="MEC/5G RAG Agent CLI")
    subparsers = parser.add_subparsers(dest="command")

    chat_parser = subparsers.add_parser("chat", help="进入多轮问答会话")
    chat_parser.add_argument("--conversation-id", default=None, help="复用指定会话 ID")
    chat_parser.add_argument("--no-ingest", action="store_true", help="启动时不自动导入 data/raw_docs")
    chat_parser.add_argument("--debug", action="store_true", help="显示检索等级、会话 ID 等调试信息")
    chat_parser.add_argument("--trace", action="store_true", help="显示可审计推理轨迹")

    agent_parser = subparsers.add_parser("mecagent", help="进入 MEC Agent 系统")
    agent_parser.add_argument("--no-ingest", action="store_true", help="启动时不自动导入 data/raw_docs")
    agent_parser.add_argument("--debug", action="store_true", help="显示检索等级、会话 ID 等调试信息")
    agent_parser.add_argument("--trace", action="store_true", help="显示可审计推理轨迹")

    ingest_parser = subparsers.add_parser("ingest", help="导入本地文档")
    ingest_parser.add_argument("--source-dir", default="data/raw_docs", help="文档目录")

    ingest_parser.add_argument("--batch-size", type=int, default=32, help="embedding/upsert batch size")

    args = parser.parse_args()
    if args.command == "ingest":
        run_ingest(args.source_dir, batch_size=args.batch_size)
        return
    if args.command == "chat":
        run_chat(
            args.conversation_id,
            auto_ingest=not args.no_ingest,
            debug=args.debug,
            trace=args.trace,
        )
        return
    if args.command == "mecagent":
        run_mecagent(auto_ingest=not args.no_ingest, debug=args.debug, trace=args.trace)
        return
    run_launcher()


def run_ingest(source_dir: str, batch_size: int = 32) -> None:
    print(f"loading documents from {source_dir} ...", flush=True)
    documents = load_documents(source_dir)
    print(f"loaded documents: {len(documents)}", flush=True)

    chunks = split_documents(documents)
    print(f"split chunks: {len(chunks)}", flush=True)

    store = VectorStore()
    print(
        f"embedding backend: {store.embedding_model.backend}; vector backend: {store.backend}",
        flush=True,
    )
    if store.embedding_model.load_error:
        print(f"embedding load warning: {store.embedding_model.load_error}", flush=True)
    if store.load_error:
        print(f"vector store warning: {store.load_error}", flush=True)

    def print_progress(done: int, total: int) -> None:
        print(f"ingested chunks: {done}/{total}", flush=True)

    stored = store.add_documents(
        chunks,
        batch_size=batch_size,
        progress_callback=print_progress,
    )
    _print_json(
        {
            "success": True,
            "message": f"ingested {len(documents)} documents",
            "chunks": stored,
            "embedding_backend": store.embedding_model.backend,
            "vector_backend": store.backend,
        }
    )


def run_chat(
    conversation_id: str | None,
    auto_ingest: bool = True,
    debug: bool = False,
    trace: bool = False,
) -> None:
    run_mecagent(auto_ingest=auto_ingest, debug=debug, trace=trace, initial_session_id=conversation_id)


def run_launcher() -> None:
    print("MEC/5G RAG Agent CLI。输入 /mecagent 进入系统，输入 /exit 退出。")
    while True:
        try:
            command = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return
        if command in {"/exit", "/quit", "exit", "quit"}:
            print("已退出。")
            return
        if command == "/mecagent":
            run_mecagent()
            return
        if command:
            print("未知命令。输入 /mecagent 进入系统，或 /exit 退出。")


def run_mecagent(
    auto_ingest: bool = True,
    debug: bool = False,
    trace: bool = False,
    initial_session_id: str | None = None,
) -> None:
    _print_start_banner()
    client = TestClient(app)
    if auto_ingest:
        response = client.post("/ingest", json={"source_dir": "data/raw_docs"})
        if response.status_code == 200 and debug:
            data = response.json()
            print(f"已导入文档：{data['message']}，chunks={data['chunks']}")
        elif response.status_code != 200:
            print(f"文档导入失败：{response.text}")

    session_id = initial_session_id
    print("已进入 MEC Agent 系统。输入 /chat 创建新会话，/chat <session_id> 继续旧会话，/session 查看历史。")

    history: list[dict[str, str]] = []
    while True:
        try:
            prompt = f"\n/mecagent:{session_id[:8] if session_id else 'no-session'}> "
            question = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return

        if not question:
            continue
        if question in {"/exit", "/quit", "exit", "quit"}:
            print("已退出。")
            return
        if question == "/help":
            _print_help()
            continue
        if question == "/history":
            if session_id:
                _print_session_messages(client, session_id)
            else:
                print("当前没有会话。输入 /chat 创建新会话。")
            continue
        if question == "/chat":
            session_id = _create_session(client)
            if session_id is None:
                continue
            history.clear()
            print(f"已创建新会话：{session_id}")
            continue
        if question.startswith("/chat "):
            target_session_id = question.split(maxsplit=1)[1].strip()
            if not target_session_id:
                print("用法：/chat <session_id>")
                continue
            session_id = target_session_id
            history.clear()
            print(f"已进入历史会话：{session_id}")
            _print_session_messages(client, session_id)
            continue
        if question == "/new":
            session_id = _create_session(client)
            if session_id is None:
                continue
            history.clear()
            print(f"已创建新会话：{session_id}")
            continue
        if question == "/session":
            _print_sessions(client)
            continue
        if question.startswith("/session delete "):
            target_session_id = question.split(maxsplit=2)[2].strip()
            if not target_session_id:
                print("用法：/session delete <session_id>")
                continue
            if _delete_session(client, target_session_id):
                if session_id == target_session_id:
                    session_id = None
                    history.clear()
                    print("已删除当前会话，当前状态切换为 no-session。")
                else:
                    print(f"已删除会话：{target_session_id}")
            continue
        if question.startswith("/session "):
            target_session_id = question.split(maxsplit=1)[1].strip()
            _print_session_messages(client, target_session_id)
            continue
        if question.startswith("/use "):
            session_id = question.split(maxsplit=1)[1].strip()
            history.clear()
            print(f"已切换到会话：{session_id}")
            continue
        if question == "/debug":
            debug = not debug
            print(f"debug={'on' if debug else 'off'}")
            continue
        if question == "/trace":
            trace = not trace
            print(f"trace={'on' if trace else 'off'}")
            continue
        if session_id is None:
            print("当前没有会话。请先输入 /chat 创建新会话，或 /chat <session_id> 进入历史会话。")
            continue

        payload = {"conversation_id": session_id, "question": question, "include_trace": trace}
        response = client.post("/chat", json=payload)
        if response.status_code != 200:
            print(f"请求失败：{response.status_code} {response.text}")
            continue

        data = response.json()
        session_id = data["conversation_id"]
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": data["answer"]})
        _print_chat_response(data, debug=debug, trace=trace)
        _confirm_cmd_approval(client, session_id, data.get("approval") or {})


def _confirm_cmd_approval(
    client: TestClient,
    conversation_id: str,
    approval: dict[str, Any],
) -> None:
    if not approval.get("approval_id"):
        return
    while True:
        try:
            choice = input("\n是否执行以上 CMD 命令？[y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n未确认，CMD 命令不会执行。")
            return
        if choice in {"y", "yes"}:
            approved = True
            break
        if choice in {"", "n", "no"}:
            approved = False
            break
        print("请输入 y（执行）或 n（拒绝）。")

    approval_type = approval.get("approval_type", "cmd_execute")
    response = client.post(
        "/files/approve" if approval_type == "file_write" else "/cmd/approve",
        json={
            "conversation_id": conversation_id,
            "approval_id": approval["approval_id"],
            "approved": approved,
        },
    )
    if response.status_code != 200:
        print(f"CMD 命令审批失败：{response.status_code} {response.text}")
        return
    result = response.json()
    print(f"CMD 命令状态：{result['status']} | exit_code={result.get('exit_code')}")
    if result.get("stdout"):
        print("stdout:\n" + result["stdout"])
    if result.get("stderr"):
        print("stderr:\n" + result["stderr"])
    if result.get("error"):
        print("error: " + result["error"])
    if result.get("answer"):
        print("\nReAct 后续结果：\n" + result["answer"])
    _print_task_timing(result.get("task_timing") or {})
    next_approval = result.get("next_approval") or {}
    if next_approval:
        _print_pending_cmd(next_approval)
        _confirm_cmd_approval(client, conversation_id, next_approval)


def _create_session(client: TestClient) -> str | None:
    response = client.post("/sessions")
    if response.status_code != 200:
        print(f"创建会话失败：{response.status_code} {response.text}")
        print("请检查 .env 中 MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE 配置，并确认 MySQL 已启动。")
        return None
    return str(response.json()["conversation_id"])


def _print_sessions(client: TestClient) -> None:
    response = client.get("/sessions")
    if response.status_code != 200:
        print(f"读取会话失败：{response.status_code} {response.text}")
        return
    sessions = response.json().get("sessions", [])
    if not sessions:
        print("暂无历史会话。输入 /chat 创建新会话。")
        return
    print("历史会话：")
    for item in sessions:
        print(
            f"- {item['id']} | messages={item['message_count']} | "
            f"updated={item['updated_at']} | {item['title']}"
        )


def _print_session_messages(client: TestClient, session_id: str) -> None:
    response = client.get(f"/sessions/{session_id}/messages")
    if response.status_code != 200:
        print(f"读取会话消息失败：{response.status_code} {response.text}")
        return
    messages = response.json().get("messages", [])
    if not messages:
        print(f"会话 {session_id} 暂无消息。")
        return
    print(f"会话 {session_id} 的历史消息：")
    for item in messages:
        content = item["content"].replace("\n", " ")
        print(f"{item['id']}. {item['role']}: {content[:180]}")


def _delete_session(client: TestClient, session_id: str) -> bool:
    response = client.delete(f"/sessions/{session_id}")
    if response.status_code == 200:
        return True
    if response.status_code == 404:
        print(f"会话不存在：{session_id}")
        return False
    print(f"删除会话失败：{response.status_code} {response.text}")
    return False


def _print_start_banner() -> None:
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"""
============================================================
   __  __ _____ ____      _                    _
  |  \\/  | ____/ ___|    / \\   __ _  ___ _ __ | |_
  | |\\/| |  _|| |       / _ \\ / _` |/ _ \\ '_ \\| __|
  | |  | | |__| |___   / ___ \\ (_| |  __/ | | | |_
  |_|  |_|_____\\____| /_/   \\_\\__, |\\___|_| |_|\\__|
                              |___/

  MEC/5G RAG Agent started at {started_at}
  输入 /chat 创建会话，/session 查看历史，/help 查看命令。
============================================================
""".strip()
    )


def _print_chat_response(data: dict[str, Any], debug: bool = False, trace: bool = False) -> None:
    print("\n" + data["answer"])
    _print_task_timing(data.get("task_timing") or {})
    approval = data.get("approval") or {}
    if approval:
        _print_pending_cmd(approval)
    if debug:
        print(
            "\n---\n"
            f"conversation_id: {data['conversation_id']}\n"
            f"standalone_question: {data['standalone_question']}\n"
            f"intent: {data['intent']} | retrieval_grade: {data['retrieval_grade']} | "
            f"faithfulness: {data['self_check'].get('faithfulness')}"
        )
    if trace and data.get("trace"):
        _print_trace(data["trace"])


def _print_task_timing(timing: dict[str, Any]) -> None:
    total = timing.get("total_elapsed_ms")
    active = timing.get("active_elapsed_ms")
    if not isinstance(total, (int, float)) or not isinstance(active, (int, float)):
        return
    print(f"\n任务耗时：总计 {_format_duration_ms(int(total))}；Agent 实际处理 {_format_duration_ms(int(active))}")


def _format_duration_ms(value: int) -> str:
    seconds, milliseconds = divmod(max(0, value), 1000)
    minutes, seconds = divmod(seconds, 60)
    if minutes:
        return f"{minutes}分{seconds}秒"
    if seconds:
        return f"{seconds}.{milliseconds // 100}秒"
    return f"{milliseconds}毫秒"


def _print_pending_cmd(approval: dict[str, Any]) -> None:
    if approval.get("approval_type") == "file_write":
        print(
            "\n待审批文件写入（尚未执行）：\n"
            f"  id: {approval.get('approval_id')}\n"
            f"  path: {approval.get('path')}\n"
            f"  overwrite: {approval.get('overwrite')}\n"
            f"  chars: {approval.get('content_chars')}\n"
            f"  preview:\n{approval.get('content_preview', '')}\n"
            f"  reason: {approval.get('rationale')}\n"
            f"  expires_at: {approval.get('expires_at')}\n"
            "随后请输入 y 确认写入，或输入 n/直接回车拒绝。"
        )
        return
    print(
        "\n待审批 CMD 命令（尚未执行）：\n"
        f"  id: {approval.get('approval_id')}\n"
        f"  command: {approval.get('command')}\n"
        f"  workdir: {approval.get('working_dir')}\n"
        f"  reason: {approval.get('rationale')}\n"
        f"  expires_at: {approval.get('expires_at')}\n"
        "随后请输入 y 确认执行，或输入 n/直接回车拒绝。"
    )


def _print_trace(trace: dict[str, Any]) -> None:
    classification = trace.get("classification", {})
    retrieval = trace.get("retrieval", {})
    print("\n--- 推理轨迹（可审计摘要）")
    print(trace.get("note", ""))
    print(f"原始问题: {trace.get('question', '')}")
    print(f"独立问题: {trace.get('standalone_question', '')}")
    print(
        "分类: "
        f"intent={classification.get('intent')} | "
        f"entities={classification.get('domain_entities')} | "
        f"need_retrieval={classification.get('need_retrieval')}"
    )
    tool_selection = trace.get("tool_selection", {})
    if tool_selection:
        print(
            "工具选择: "
            f"tool={tool_selection.get('selected_tool')} | "
            f"source={tool_selection.get('source')} | "
            f"args={tool_selection.get('args')}"
        )
        if tool_selection.get("reason"):
            print(f"选择原因: {tool_selection.get('reason')}")
    print("检索 Query:")
    for index, query in enumerate(retrieval.get("queries", []), start=1):
        print(f"  {index}. {query}")
    print(
        "检索评估: "
        f"grade={retrieval.get('grade')} | "
        f"score={retrieval.get('score')} | "
        f"retry_count={retrieval.get('retry_count')}"
    )
    print(f"证据来源: {retrieval.get('sources', [])}")
    print("证据事实:")
    for index, fact in enumerate(trace.get("evidence_facts", []), start=1):
        print(f"  {index}. {fact.get('claim')}（{fact.get('source')}）")
    self_check = trace.get("self_check", {})
    print(
        "自检: "
        f"faithfulness={self_check.get('faithfulness')} | "
        f"need_more_retrieval={self_check.get('need_more_retrieval')} | "
        f"action={self_check.get('action')}"
    )


def _print_help() -> None:
    print(
        """
可用命令：
  /mecagent        从 CLI 首页进入系统
  /chat            创建一个新会话，并分配 conversation_id
  /chat <id>       进入指定历史会话继续聊天
  /session         查看 MySQL 中的历史会话
  /session <id>    查看指定会话的历史消息
  /session delete <id> 删除指定会话及其历史消息
  /use <id>        切换到指定历史会话继续聊天
  /history         查看当前会话历史消息
  /debug           显示/隐藏调试信息
  /trace           显示/隐藏可审计推理轨迹
  /exit            退出

创建或切换会话后，直接输入问题即可继续当前多轮会话，例如：
  NEF 返回成功，但流量没有进入 MEC APP，可能是什么原因？
  那应该先查哪里？
  这和 MEPM 有关系吗？
""".strip()
    )


def _print_history(history: list[dict[str, str]]) -> None:
    if not history:
        print("当前还没有会话历史。")
        return
    for index, item in enumerate(history, start=1):
        content = item["content"].replace("\n", " ")
        print(f"{index}. {item['role']}: {content[:180]}")


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出。")
        sys.exit(0)
