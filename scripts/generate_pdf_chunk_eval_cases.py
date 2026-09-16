"""Generate human-reviewable chunk-level retrieval cases from one PDF corpus.

The generator uses the configured DeepSeek model to write one Chinese question
for each selected chunk. Every returned required term must occur verbatim in the
labelled chunk, so malformed or hallucinated cases are discarded automatically.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402
from app.rag.document_loader import load_documents  # noqa: E402
from app.rag.text_splitter import split_documents  # noqa: E402


CATEGORIES = {"concept", "procedure", "resource", "http", "data_model", "security", "openapi"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate chunk-labelled PDF retrieval cases.")
    parser.add_argument("--source-dir", default="data/raw_docs")
    parser.add_argument("--output", default="data/evals/ts29522_chunk_cases_200.json")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()

    if not settings.deepseek_api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required to generate questions")

    chunks = split_documents(load_documents(args.source_dir))
    candidates = _select_candidates(chunks, max(args.count * 2, args.count + 80))
    cases: list[dict[str, Any]] = []
    used_questions: set[str] = set()
    used_indexes: set[int] = set()

    for start in range(0, len(candidates), args.batch_size):
        if len(cases) >= args.count:
            break
        batch = [item for item in candidates[start : start + args.batch_size] if item[0] not in used_indexes]
        generated = _generate_batch(batch)
        for value in generated:
            case = _validate_case(value, chunks)
            if case is None:
                continue
            fingerprint = re.sub(r"\s+", "", case["question"]).casefold()
            index = case["expected_chunk_indexes"][0]
            if fingerprint in used_questions or index in used_indexes:
                continue
            cases.append(case)
            used_questions.add(fingerprint)
            used_indexes.add(index)
            if len(cases) >= args.count:
                break
        print(f"generated {len(cases)}/{args.count}", flush=True)

    if len(cases) < args.count:
        raise SystemExit(f"only generated {len(cases)} valid cases; rerun with a smaller batch or more candidates")

    for number, case in enumerate(cases, start=1):
        case["id"] = f"ts29522-{number:03d}"

    output = PROJECT_ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases to {output}")
    return 0


def _select_candidates(chunks: list[dict[str, Any]], limit: int) -> list[tuple[int, str]]:
    eligible: list[tuple[int, str]] = []
    for index, chunk in enumerate(chunks):
        content = str(chunk.get("content", ""))
        if index < 100 or len(content) < 450:
            continue
        if content.count("................................................................") >= 2:
            continue
        if len(re.findall(r"[A-Za-z0-9]", content)) < 220:
            continue
        eligible.append((index, content))
    if len(eligible) <= limit:
        return eligible

    # Evenly sample the whole specification so procedures, data models and the
    # OpenAPI annex are all represented instead of clustering around one API.
    selected: list[tuple[int, str]] = []
    for position in range(limit):
        source_index = round(position * (len(eligible) - 1) / max(limit - 1, 1))
        selected.append(eligible[source_index])
    return selected


def _generate_batch(batch: list[tuple[int, str]]) -> list[dict[str, Any]]:
    if not batch:
        return []
    from openai import OpenAI

    client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
    payload = [{"chunk_index": index, "content": content} for index, content in batch]
    messages = [
        {
            "role": "system",
            "content": (
                "你负责为3GPP TS 29.522技术规范生成高质量中文RAG检索测试题。"
                "对每个输入chunk恰好生成一道题，答案必须能仅依据该chunk得到。"
                "问题要询问技术含义、流程、HTTP方法、资源、字段或安全要求，不要询问页码、章节号，"
                "不要使用‘根据文档’等提示语。required_terms必须是原chunk中逐字存在的1至3个非通用术语。"
                "只返回JSON对象，结构为{\"cases\":[{\"chunk_index\":整数,\"question\":中文问题,"
                "\"gold_answer\":不超过160字的中文参考答案,\"required_terms\":[字符串],"
                "\"category\":concept|procedure|resource|http|data_model|security|openapi}]}。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=settings.deepseek_model,
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
                max_tokens=8000,
            )
            value = json.loads(response.choices[0].message.content or "{}")
            cases = value.get("cases", [])
            return cases if isinstance(cases, list) else []
        except Exception as exc:
            if attempt == 2:
                print(f"batch failed: {exc}", file=sys.stderr, flush=True)
                return []
            time.sleep(2 ** attempt)
    return []


def _validate_case(value: Any, chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        index = int(value.get("chunk_index"))
    except (TypeError, ValueError):
        return None
    if not 0 <= index < len(chunks):
        return None
    question = str(value.get("question", "")).strip()
    answer = str(value.get("gold_answer", "")).strip()
    category = str(value.get("category", "concept")).strip()
    terms = value.get("required_terms", [])
    if len(question) < 8 or not answer or category not in CATEGORIES:
        return None
    if not isinstance(terms, list) or not 1 <= len(terms) <= 3:
        return None
    terms = [str(term).strip() for term in terms if str(term).strip()]
    content = str(chunks[index].get("content", ""))
    if not terms or any(term.casefold() not in content.casefold() for term in terms):
        return None
    return {
        "id": "pending",
        "question": question,
        "expected_source": str(chunks[index].get("source", "")),
        "expected_chunk_indexes": [index],
        "required_terms": terms,
        "gold_answer": answer[:500],
        "category": category,
    }


if __name__ == "__main__":
    raise SystemExit(main())
