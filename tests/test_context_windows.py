from __future__ import annotations

from app.rag.context_windows import build_context_windows, window_contains_gold


def _doc(index: int, section: str = "4.1 Procedure") -> dict:
    return {
        "content": f"content-{index}",
        "source": "spec.pdf",
        "score": 1.0,
        "metadata": {
            "source": "spec.pdf",
            "section": section,
            "chunk_index": index,
            "chunk_id": f"chunk-{index}",
            "page_start": 10,
        },
    }


def test_window_contains_previous_centre_and_next_chunk() -> None:
    corpus = [_doc(0), _doc(1), _doc(2)]

    window = build_context_windows([corpus[1]], corpus, radius=1)[0]

    assert window["metadata"]["center_chunk_id"] == "chunk-1"
    assert window["metadata"]["member_chunk_ids"] == [
        "chunk-0",
        "chunk-1",
        "chunk-2",
    ]
    assert "[CENTRE chunk 1]" in window["content"]


def test_window_never_crosses_a_section_boundary() -> None:
    corpus = [_doc(0), _doc(1), _doc(2, "4.2 Response")]

    window = build_context_windows([corpus[1]], corpus, radius=1)[0]

    assert window["metadata"]["member_chunk_ids"] == ["chunk-0", "chunk-1"]


def test_neighbor_gold_counts_as_a_window_hit() -> None:
    corpus = [_doc(0), _doc(1), _doc(2)]
    window = build_context_windows([corpus[1]], corpus, radius=1)[0]

    assert window_contains_gold(
        window,
        expected_chunk_ids={"chunk-2"},
        expected_chunk_indexes=set(),
    )
