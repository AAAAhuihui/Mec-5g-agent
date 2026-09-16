"""Remap legacy character-chunk labels to stable section/token chunk IDs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.document_loader import load_document_text, load_documents  # noqa: E402
from app.rag.keyword_search import tokenize_terms  # noqa: E402
from app.rag.text_splitter import legacy_split_text, split_documents  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default="data/raw_docs")
    parser.add_argument("--cases", default="data/evals/ts29522_chunk_cases_200.json")
    parser.add_argument(
        "--output",
        default="data/evals/ts29522_structured_cases_200.json",
    )
    parser.add_argument("--max-gold", type=int, default=3)
    args = parser.parse_args()

    if args.max_gold < 1:
        raise SystemExit("--max-gold must be positive")
    cases_path = _resolve(args.cases)
    source_dir = _resolve(args.source_dir)
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise SystemExit("cases must be a non-empty JSON list")

    new_chunks = split_documents(load_documents(str(source_dir)))
    by_source: dict[str, list[dict[str, Any]]] = {}
    for chunk in new_chunks:
        by_source.setdefault(str(chunk.get("source", "")), []).append(chunk)

    legacy_by_source: dict[str, list[str]] = {}
    remapped: list[dict[str, Any]] = []
    for case in cases:
        source = str(case.get("expected_source", ""))
        if source not in legacy_by_source:
            source_path = source_dir / source
            if not source_path.exists():
                raise SystemExit(f"source file not found for case {case.get('id')}: {source_path}")
            legacy_by_source[source] = legacy_split_text(load_document_text(source_path))
        old_indexes = case.get("expected_chunk_indexes", [])
        if not old_indexes:
            raise SystemExit(f"case {case.get('id')} has no legacy chunk index")
        old_texts = []
        for index in old_indexes:
            try:
                old_texts.append(legacy_by_source[source][int(index)])
            except (IndexError, TypeError, ValueError) as exc:
                raise SystemExit(
                    f"case {case.get('id')} has invalid legacy chunk index {index}"
                ) from exc
        gold_chunks = map_gold_chunks(
            case,
            "\n\n".join(old_texts),
            by_source.get(source, []),
            max_gold=args.max_gold,
        )
        if not gold_chunks:
            raise SystemExit(f"no new Gold chunk found for case {case.get('id')}")
        mapped = dict(case)
        mapped["legacy_chunk_indexes"] = list(old_indexes)
        mapped["expected_chunk_indexes"] = [
            int(chunk["metadata"]["chunk_index"]) for chunk in gold_chunks
        ]
        mapped["expected_chunk_ids"] = [
            str(chunk["metadata"]["chunk_id"]) for chunk in gold_chunks
        ]
        mapped["gold_sections"] = list(
            dict.fromkeys(str(chunk["metadata"]["section"]) for chunk in gold_chunks)
        )
        mapped["gold_pages"] = sorted(
            {
                int(chunk["metadata"]["page_start"])
                for chunk in gold_chunks
                if "page_start" in chunk["metadata"]
            }
        )
        remapped.append(mapped)

    output = _resolve(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(remapped, ensure_ascii=False, indent=2), encoding="utf-8")
    multi_gold = sum(len(case["expected_chunk_ids"]) > 1 for case in remapped)
    total_labels = sum(len(case["expected_chunk_ids"]) for case in remapped)
    print(f"legacy chunks: {sum(len(value) for value in legacy_by_source.values())}")
    print(f"new chunks: {len(new_chunks)}")
    print(f"remapped cases: {len(remapped)}")
    print(f"cases with multiple Gold chunks: {multi_gold}")
    print(f"Gold labels: {total_labels}")
    print(f"wrote: {output}")
    return 0


def map_gold_chunks(
    case: dict[str, Any],
    legacy_gold_text: str,
    chunks: list[dict[str, Any]],
    *,
    max_gold: int = 5,
) -> list[dict[str, Any]]:
    if not chunks:
        return []
    required_terms = [
        str(term).strip().casefold()
        for term in case.get("required_terms", [])
        if str(term).strip()
    ]
    reference = " ".join(
        [
            legacy_gold_text,
            str(case.get("question", "")),
            str(case.get("gold_answer", "")),
            " ".join(required_terms),
        ]
    )
    reference_terms = Counter(tokenize_terms(reference))
    scores: list[tuple[float, int, dict[str, Any]]] = []
    for chunk in chunks:
        content = str(chunk.get("content", ""))
        lowered = content.casefold()
        coverage = sum(_contains_term(lowered, term) for term in required_terms)
        content_terms = Counter(tokenize_terms(content))
        common = sum((content_terms & reference_terms).values())
        precision = common / max(sum(content_terms.values()), 1)
        recall = common / max(sum(reference_terms.values()), 1)
        all_required = bool(required_terms) and coverage == len(required_terms)
        score = (8.0 if all_required else 0.0) + 2.0 * coverage + 2.0 * precision + recall
        scores.append((score, coverage, chunk))

    scores.sort(
        key=lambda item: (
            -item[0],
            -item[1],
            int(item[2].get("metadata", {}).get("chunk_index", 0)),
        )
    )
    full_coverage = [
        item for item in scores if required_terms and item[1] == len(required_terms)
    ]
    if full_coverage:
        best = full_coverage[0][0]
        selected = [item[2] for item in full_coverage if item[0] >= best - 0.55]
        return selected[:max_gold]

    best_coverage = scores[0][1]
    best_score = scores[0][0]
    # When a smaller v2 chunk separates two required terms, label the strongest
    # evidence chunks for each term. This is why a case may have multiple Golds.
    selected = [
        item[2]
        for item in scores
        if item[1] == best_coverage and item[0] >= best_score - 0.35
    ][:max_gold]
    if required_terms and best_coverage < len(required_terms):
        for term in required_terms:
            if any(
                _contains_term(str(chunk.get("content", "")).casefold(), term)
                for chunk in selected
            ):
                continue
            term_match = next(
                (
                    item[2]
                    for item in scores
                    if _contains_term(str(item[2].get("content", "")).casefold(), term)
                ),
                None,
            )
            if term_match is not None and term_match not in selected:
                selected.append(term_match)
            if len(selected) >= max_gold:
                break
    return selected[:max_gold]


def _contains_term(content: str, term: str) -> bool:
    if re.fullmatch(r"[a-z0-9_]+", term) and len(term) <= 3:
        return re.search(
            rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])",
            content,
        ) is not None
    return term in content


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
