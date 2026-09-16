from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import settings


TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def run_web_search(
    question: str,
    tool_args: dict[str, Any] | None = None,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Search Tavily and convert results into the workflow's evidence format."""
    tool_args = tool_args or {}
    query = str(tool_args.get("query") or question).strip()
    limit = max(1, min(max_results or settings.web_search_max_results, 10))

    if not settings.tavily_api_key:
        return _failure_result(
            query,
            "联网搜索尚未配置：请在 .env 中设置 TAVILY_API_KEY 后重试。",
        )

    payload = json.dumps(
        {
            "query": query,
            "search_depth": "basic",
            "max_results": limit,
            "include_answer": False,
            "include_raw_content": False,
        }
    ).encode("utf-8")
    request = Request(
        TAVILY_SEARCH_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.tavily_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return _failure_result(query, f"联网搜索请求失败（HTTP {error.code}）。")
    except (URLError, TimeoutError) as error:
        reason = error.reason if isinstance(error, URLError) else "请求超时"
        return _failure_result(query, f"联网搜索不可用：{reason}。")
    except json.JSONDecodeError:
        return _failure_result(query, "联网搜索返回了无法解析的响应。")

    results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(results, list):
        return _failure_result(query, "联网搜索没有返回可用结果。")

    docs = _results_to_docs(results)
    return {
        "query": query,
        "docs": docs,
        "facts": [
            {"claim": str(doc["content"]), "source": str(doc["source"])}
            for doc in docs
        ],
        "answer": build_web_search_answer(query, docs),
    }


def _results_to_docs(results: list[Any]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url", "")).strip()
        title = str(result.get("title", "")).strip()
        content = str(result.get("content", "")).strip()
        if not url or not content:
            continue
        docs.append(
            {
                "content": content,
                "source": url,
                "score": float(result.get("score", 0.0) or 0.0),
                "metadata": {
                    "tool": "web_search",
                    "title": title or url,
                    "url": url,
                },
            }
        )
    return docs


def _failure_result(query: str, message: str) -> dict[str, Any]:
    return {"query": query, "docs": [], "facts": [], "answer": message}


def build_web_search_answer(query: str, docs: list[dict[str, Any]]) -> str:
    if not docs:
        return f"未找到与“{query}”相关的联网搜索结果。"

    items = []
    for index, doc in enumerate(docs, start=1):
        title = str(doc.get("metadata", {}).get("title", doc["source"]))
        content = str(doc["content"]).replace("\n", " ").strip()
        items.append(f"{index}. {title}\n{content}\n来源：{doc['source']}")
    return "联网检索结果：\n\n" + "\n\n".join(items)
