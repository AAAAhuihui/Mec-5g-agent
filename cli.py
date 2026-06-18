from __future__ import annotations

import argparse
import json
import sys
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
    parser.print_help()


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
    client = TestClient(app)
    if auto_ingest:
        response = client.post("/ingest", json={"source_dir": "data/raw_docs"})
        if response.status_code == 200 and debug:
            data = response.json()
            print(f"已导入文档：{data['message']}，chunks={data['chunks']}")
        elif response.status_code != 200:
            print(f"文档导入失败：{response.text}")

    session_id = conversation_id
    print("进入 MEC/5G RAG Agent 会话。输入 /exit 退出。")

    history: list[dict[str, str]] = []
    while True:
        try:
            question = input("\n/chat> ").strip()
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
            _print_history(history)
            continue
        if question == "/new":
            session_id = None
            history.clear()
            print("已开启新会话。")
            continue
        if question == "/debug":
            debug = not debug
            print(f"debug={'on' if debug else 'off'}")
            continue
        if question == "/trace":
            trace = not trace
            print(f"trace={'on' if trace else 'off'}")
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


def _print_chat_response(data: dict[str, Any], debug: bool = False, trace: bool = False) -> None:
    print("\n" + data["answer"])
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
  /help      查看帮助
  /history   查看当前 CLI 内的历史
  /new       开启新会话
  /debug     显示/隐藏调试信息
  /trace     显示/隐藏可审计推理轨迹
  /exit      退出

直接输入问题即可继续当前多轮会话，例如：
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
