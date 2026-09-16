from __future__ import annotations

from pathlib import Path

from app.evaluation import load_eval_cases


def test_mec_5g_eval_set_contains_twenty_labelled_cases() -> None:
    project_root = Path(__file__).resolve().parents[1]
    cases = load_eval_cases(project_root / "data" / "evals" / "mec_5g_cases.json")

    assert len(cases) == 20
    assert {case["expected_source"] for case in cases} == {
        "mec_5g_intro.md",
        "traffic_influence.md",
        "troubleshooting.md",
    }
    assert all(case["required_terms"] for case in cases)


def test_ts29522_eval_set_contains_two_hundred_chunk_labelled_cases() -> None:
    project_root = Path(__file__).resolve().parents[1]
    cases = load_eval_cases(
        project_root / "data" / "evals" / "ts29522_chunk_cases_200.json"
    )

    assert len(cases) == 200
    assert len({case["id"] for case in cases}) == 200
    assert len({case["question"] for case in cases}) == 200
    assert all(case["expected_source"] == "29522-gh0.pdf" for case in cases)
    assert all(case["expected_chunk_indexes"] for case in cases)
    assert all(case["required_terms"] for case in cases)


def test_chunk_labelled_case_is_loaded() -> None:
    from app import evaluation

    path = Path(__file__).resolve().parent / "_chunk_eval_case.json"
    path.write_text(
        '[{"id":"one","question":"q","expected_source":"a.pdf",'
        '"expected_chunk_indexes":[3,4],"required_terms":["NEF"]}]',
        encoding="utf-8",
    )
    try:
        cases = evaluation.load_eval_cases(path)
    finally:
        path.unlink(missing_ok=True)

    assert cases[0]["expected_chunk_indexes"] == [3, 4]


def test_multiple_stable_gold_chunk_ids_are_loaded() -> None:
    from app import evaluation

    path = Path(__file__).resolve().parent / "_multi_gold_eval_case.json"
    path.write_text(
        '[{"id":"one","question":"q","expected_source":"a.pdf",'
        '"expected_chunk_ids":["gold-a","gold-b"],"required_terms":["NEF"]}]',
        encoding="utf-8",
    )
    try:
        cases = evaluation.load_eval_cases(path)
    finally:
        path.unlink(missing_ok=True)

    assert cases[0]["expected_chunk_ids"] == ["gold-a", "gold-b"]


def test_chunk_labels_drive_recall_and_mrr(monkeypatch) -> None:
    from app import evaluation

    monkeypatch.setattr(evaluation, "load_documents", lambda _: [{"content": "x", "source": "a.pdf", "path": "a.pdf"}])
    monkeypatch.setattr(
        evaluation,
        "split_documents",
        lambda _: [
            {"content": "noise", "source": "a.pdf", "metadata": {"source": "a.pdf", "chunk_index": 0}},
            {"content": "NEF answer", "source": "a.pdf", "metadata": {"source": "a.pdf", "chunk_index": 1}},
        ],
    )

    class _Embedding:
        backend = "test"
        model_name = "test"
        load_error = None

        @staticmethod
        def embed(texts):
            return [[1.0, 0.0], [0.5, 0.5]]

        @staticmethod
        def embed_query(text):
            return [1.0, 0.0]

    report = evaluation.evaluate_source_dir(
        "unused",
        [{"id": "one", "question": "q", "expected_source": "a.pdf", "expected_chunk_indexes": [1], "required_terms": ["NEF"]}],
        top_k=2,
        embedding_model=_Embedding(),
    )

    result = report["cases"][0]
    assert result["retrieval_hit"] is True
    assert result["first_relevant_rank"] == 1
    assert result["reciprocal_rank"] == 1.0
    assert result["window_hit"] is True


def test_neighbor_expansion_is_measured_separately_from_raw_candidate_recall(monkeypatch) -> None:
    from app import evaluation

    monkeypatch.setattr(
        evaluation,
        "load_documents",
        lambda _: [{"content": "x", "source": "a.pdf", "path": "a.pdf"}],
    )
    monkeypatch.setattr(
        evaluation,
        "split_documents",
        lambda _: [
            {
                "content": "centre",
                "source": "a.pdf",
                "metadata": {
                    "source": "a.pdf", "section": "4.1", "chunk_index": 0,
                    "chunk_id": "centre",
                },
            },
            {
                "content": "NEF answer",
                "source": "a.pdf",
                "metadata": {
                    "source": "a.pdf", "section": "4.1", "chunk_index": 1,
                    "chunk_id": "gold",
                },
            },
        ],
    )

    class _Embedding:
        backend = "test"
        model_name = "test"
        load_error = None

        @staticmethod
        def embed(texts):
            return [[1.0, 0.0], [0.0, 1.0]]

        @staticmethod
        def embed_query(text):
            return [1.0, 0.0]

    report = evaluation.evaluate_source_dir(
        "unused",
        [{
            "id": "one", "question": "q", "expected_source": "a.pdf",
            "expected_chunk_ids": ["gold"], "required_terms": ["NEF"],
        }],
        top_k=1,
        vector_candidate_k=1,
        bm25_candidate_k=1,
        fused_candidate_k=1,
        reranker_backend="lightweight",
        embedding_model=_Embedding(),
    )

    assert report["metrics"]["candidate_recall"] == 0.0
    assert report["metrics"]["expanded_candidate_recall"] == 1.0
    assert report["metrics"]["window_recall_at_k"] == 1.0
    assert report["cases"][0]["evidence_usable"] is True


def test_report_marks_a_pool_no_larger_than_top_k_as_not_resume_ready(monkeypatch) -> None:
    from app import evaluation

    monkeypatch.setattr(evaluation, "load_documents", lambda _: [{"content": "NEF AF", "source": "a.md", "path": "a.md"}])
    monkeypatch.setattr(
        evaluation,
        "split_documents",
        lambda _: [{"content": "NEF AF", "source": "a.md", "metadata": {"source": "a.md", "chunk_index": 0}}],
    )

    class _Embedding:
        backend = "test"
        model_name = "test"
        load_error = None

        @staticmethod
        def embed(texts):
            return [[1.0, 0.0] for _ in texts]

        @staticmethod
        def embed_query(text):
            return [1.0, 0.0]

    report = evaluation.evaluate_source_dir(
        "unused",
        [{"id": "one", "question": "NEF", "expected_source": "a.md", "required_terms": ["NEF"]}],
        top_k=6,
        embedding_model=_Embedding(),
    )

    assert report["metric_validity"]["suitable_for_resume_claim"] is False


def test_raw_union_recall_is_measured_before_rrf(monkeypatch) -> None:
    from app import evaluation

    monkeypatch.setattr(
        evaluation,
        "load_documents",
        lambda _: [{"content": "x", "source": "a.pdf", "path": "a.pdf"}],
    )
    monkeypatch.setattr(
        evaluation,
        "split_documents",
        lambda _: [
            {
                "content": "noise",
                "source": "a.pdf",
                "metadata": {"source": "a.pdf", "chunk_index": 0},
            },
            {
                "content": "NIDD externalGroupId",
                "source": "a.pdf",
                "metadata": {"source": "a.pdf", "chunk_index": 1},
            },
        ],
    )

    class _Embedding:
        backend = "test"
        model_name = "test"
        load_error = None

        @staticmethod
        def embed(texts):
            return [[1.0, 0.0], [0.0, 1.0]]

        @staticmethod
        def embed_query(text):
            return [1.0, 0.0]

    report = evaluation.evaluate_source_dir(
        "unused",
        [
            {
                "id": "one",
                "question": "externalGroupId",
                "expected_source": "a.pdf",
                "expected_chunk_indexes": [1],
                "required_terms": ["externalGroupId"],
            }
        ],
        top_k=1,
        vector_candidate_k=1,
        bm25_candidate_k=1,
        fused_candidate_k=2,
        reranker_backend="lightweight",
        embedding_model=_Embedding(),
    )

    assert report["metrics"]["vector_recall"] == 0.0
    assert report["metrics"]["bm25_recall"] == 1.0
    assert report["metrics"]["union_recall"] == 1.0
