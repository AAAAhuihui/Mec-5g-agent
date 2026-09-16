"""Evaluate the local hybrid RAG retriever against labelled MEC/5G cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402
from app.evaluation import evaluate_source_dir, load_eval_cases  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run labelled offline RAG retrieval evaluation.")
    parser.add_argument("--source-dir", default="examples", help="Directory containing the evaluation knowledge base.")
    parser.add_argument(
        "--cases", default="data/evals/mec_5g_cases.json", help="JSON file containing labelled evaluation cases."
    )
    parser.add_argument("--top-k", type=int, default=settings.top_k, help="Number of retrieved chunks per question.")
    parser.add_argument("--vector-candidate-k", type=int, default=settings.rag_vector_candidate_k)
    parser.add_argument("--bm25-candidate-k", type=int, default=settings.rag_bm25_candidate_k)
    parser.add_argument("--fused-candidate-k", type=int, default=settings.rag_fused_candidate_k)
    parser.add_argument(
        "--reranker-backend",
        choices=["lightweight", "cross-encoder"],
        default=settings.reranker_backend,
    )
    parser.add_argument("--json", action="store_true", help="Print complete per-case results as JSON.")
    parser.add_argument("--output", help="Write the complete JSON report to this path.")
    parser.add_argument("--limit", type=int, help="Evaluate only this many cases.")
    parser.add_argument(
        "--sample",
        choices=["head", "even"],
        default="head",
        help="How to select cases when --limit is used.",
    )
    args = parser.parse_args()

    cases = load_eval_cases(args.cases)
    if args.limit is not None:
        if args.limit < 1:
            raise SystemExit("--limit must be positive")
        if args.limit < len(cases):
            if args.sample == "even":
                indexes = [
                    round(position * (len(cases) - 1) / max(args.limit - 1, 1))
                    for position in range(args.limit)
                ]
                cases = [cases[index] for index in indexes]
            else:
                cases = cases[: args.limit]

    report = evaluate_source_dir(
        args.source_dir,
        cases,
        top_k=args.top_k,
        vector_candidate_k=args.vector_candidate_k,
        bm25_candidate_k=args.bm25_candidate_k,
        fused_candidate_k=args.fused_candidate_k,
        reranker_backend=args.reranker_backend,
    )
    metrics = report["metrics"]
    pipeline = report["pipeline"]
    print("Offline RAG retrieval evaluation")
    print(f"Embedding backend: {report['embedding_backend']}")
    print(f"Knowledge chunks: {report['document_chunks']}; cases: {report['case_count']}; Top-K: {report['top_k']}")
    print(
        "Pipeline: "
        f"vector Top-{pipeline['vector_candidate_k']} + "
        f"BM25 Top-{pipeline['bm25_candidate_k']} -> "
        f"RRF Top-{pipeline['fused_candidate_k']} -> "
        f"neighbor window ±{pipeline['neighbor_radius']} -> "
        f"{pipeline['reranker_backend']} Window Top-{report['top_k']}"
    )
    print(
        f"Vector Recall@{pipeline['vector_candidate_k']}: "
        f"{metrics['vector_recall']:.2%} "
        f"({metrics['vector_hit_count']}/{report['case_count']})"
    )
    print(
        f"BM25 Recall@{pipeline['bm25_candidate_k']}: "
        f"{metrics['bm25_recall']:.2%} "
        f"({metrics['bm25_hit_count']}/{report['case_count']})"
    )
    print(
        "Raw union Recall "
        f"(vector Top-{pipeline['vector_candidate_k']} ∪ "
        f"BM25 Top-{pipeline['bm25_candidate_k']}): "
        f"{metrics['union_recall']:.2%} "
        f"({metrics['union_hit_count']}/{report['case_count']})"
    )
    print(
        f"Candidate Recall@{pipeline['fused_candidate_k']}: "
        f"{metrics['candidate_recall']:.2%} "
        f"({metrics['candidate_hit_count']}/{report['case_count']})"
    )
    print(
        f"Expanded Recall@{pipeline['fused_candidate_k']}: "
        f"{metrics['expanded_candidate_recall']:.2%} "
        f"({metrics['expanded_candidate_hit_count']}/{report['case_count']})"
    )
    print(
        f"Window Recall@{report['top_k']}: {metrics['window_recall_at_k']:.2%} "
        f"({metrics['retrieval_hit_count']}/{report['case_count']})"
    )
    print(f"Window MRR@{report['top_k']}: {metrics['window_mrr_at_k']:.4f}")
    print(
        "Evidence usable rate: "
        f"{metrics['evidence_usable_rate']:.2%} ({metrics['evidence_usable_count']}/{report['case_count']})"
    )
    validity = report["metric_validity"]
    if not validity["suitable_for_resume_claim"]:
        print("WARNING: " + validity["warning"])
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report written to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
