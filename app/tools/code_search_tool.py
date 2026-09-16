from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.config import BASE_DIR


CODE_FILE_EXTENSIONS = "py|js|ts|tsx|java|go|rs|c|cpp|h|hpp|cs|sql"
TEXT_SUFFIXES = {
    ".py",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".sql",
    ".sh",
    ".ps1",
}

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}


def run_code_search(
    question: str,
    tool_args: dict[str, Any] | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
    tool_args = tool_args or {}
    repo_path = Path(str(tool_args.get("repo_path") or extract_code_path(question)))
    query = str(tool_args.get("query") or extract_code_query(question, repo_path))
    results = search_code(query, str(repo_path), max_results=max_results)

    return {
        "repo_path": repo_path,
        "query": query,
        "results": results,
        "docs": code_results_to_docs(results),
        "facts": code_results_to_facts(results),
        "answer": build_code_search_answer(query, repo_path, results, max_results=max_results),
    }


def search_code(query: str, repo_path: str, max_results: int = 20) -> list[dict[str, str]]:
    query = query.strip()
    if not query:
        return [{"status": "invalid_query", "message": "query is empty"}]

    root = Path(repo_path)
    if not root.exists():
        return [{"status": "not_found", "message": f"repo_path not found: {repo_path}"}]

    files = [root] if root.is_file() else _iter_source_files(root)
    matches: list[dict[str, str]] = []
    lowered_query = query.lower()

    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue

        for line_no, line in enumerate(lines, start=1):
            if lowered_query not in line.lower():
                continue
            matches.append(
                {
                    "status": "matched",
                    "query": query,
                    "repo_path": str(root),
                    "file": str(path),
                    "line": str(line_no),
                    "content": line.strip(),
                }
            )
            if len(matches) >= max_results:
                return matches

    if matches:
        return matches
    return [
        {
            "status": "no_match",
            "query": query,
            "repo_path": str(root),
            "message": f"no code matches for query: {query}",
        }
    ]


def extract_code_path(question: str) -> Path:
    file_path_match = re.search(
        rf"[A-Za-z]:[\\/].+?\.(?:{CODE_FILE_EXTENSIONS})",
        question,
        flags=re.IGNORECASE,
    )
    if file_path_match:
        return Path(file_path_match.group(0).rstrip("，。；;"))

    path_match = re.search(r"[A-Za-z]:[\\/][^\s\"'`，。；;]+", question)
    if path_match:
        return Path(path_match.group(0).rstrip("，。；;"))

    relative_match = re.search(
        rf"(?:[\w.-]+[\\/])+[\w.-]+\.(?:{CODE_FILE_EXTENSIONS})",
        question,
        flags=re.IGNORECASE,
    )
    if relative_match:
        candidate = BASE_DIR / relative_match.group(0).rstrip("，。；;").replace("\\", "/")
        if candidate.exists():
            return candidate

    return BASE_DIR


def extract_code_query(question: str, repo_path: Path) -> str:
    quoted_values = re.findall(r"[`\"'“”‘’]([^`\"'“”‘’]+)[`\"'“”‘’]", question)
    for value in quoted_values:
        candidate = value.strip()
        if not looks_like_path(candidate):
            return candidate

    explicit = re.search(r"(?:关键词|查找|搜索|定位)\s*[:：]?\s*([A-Za-z_][\w.:-]*)", question)
    if explicit:
        return explicit.group(1).strip()

    question_without_path = question.replace(str(repo_path), " ")
    tokens = re.findall(r"[A-Za-z_][\w.:-]*", question_without_path)
    ignored = {"repo", "repository", "path", "file", "code", "python"}
    for token in tokens:
        if token.lower() not in ignored:
            return token
    return question.strip()


def looks_like_path(value: str) -> bool:
    if re.search(r"[A-Za-z]:[\\/]", value):
        return True
    return bool(re.search(r"(?:[\w.-]+[\\/])+[\w.-]+", value))


def code_results_to_docs(results: list[dict[str, str]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for result in results:
        if result.get("status") != "matched":
            continue
        source = f"{result.get('file')}:{result.get('line')}"
        docs.append(
            {
                "content": result.get("content", ""),
                "source": source,
                "score": 1.0,
                "metadata": {
                    "tool": "code_search",
                    "query": result.get("query", ""),
                    "repo_path": result.get("repo_path", ""),
                    "file": result.get("file", ""),
                    "line": result.get("line", ""),
                },
            }
        )
    return docs


def code_results_to_facts(results: list[dict[str, str]]) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    for result in results:
        if result.get("status") != "matched":
            continue
        source = f"{result.get('file')}:{result.get('line')}"
        facts.append({"claim": result.get("content", ""), "source": source})
    return facts


def build_code_search_answer(
    query: str,
    repo_path: Path,
    results: list[dict[str, str]],
    max_results: int = 20,
) -> str:
    matches = [result for result in results if result.get("status") == "matched"]
    if not matches:
        message = results[0].get("message", "没有找到匹配结果。") if results else "没有找到匹配结果。"
        return (
            "结论：\n"
            f"没有在给定代码位置找到关键词 `{query}` 的命中结果。\n\n"
            "检索范围：\n"
            f"{repo_path}\n\n"
            "详细说明：\n"
            f"{message}\n\n"
            "下一步建议：\n"
            "请确认代码路径是否存在，或者把关键词用反引号/引号包起来后重新提问。"
        )

    lines = "\n".join(
        f"{index + 1}. {result.get('file')}:{result.get('line')} -> {result.get('content')}"
        for index, result in enumerate(matches[:max_results])
    )
    return (
        "结论：\n"
        f"在给定代码位置找到了关键词 `{query}` 的命中结果。\n\n"
        "检索范围：\n"
        f"{repo_path}\n\n"
        "定位结果：\n"
        f"{lines}\n\n"
        "下一步建议：\n"
        "可以根据上述文件和行号继续查看调用关系，或进一步搜索函数名、类名、异常信息等更精确的关键词。"
    )


def _iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            files.append(path)
    return files
