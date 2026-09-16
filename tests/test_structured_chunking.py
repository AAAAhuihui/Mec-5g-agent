from __future__ import annotations

from app.rag.text_splitter import count_tokens, split_documents, split_text


def test_split_text_respects_token_budget() -> None:
    text = "4.1 Resource definition\n" + "The NEF shall expose the resource. " * 100

    chunks = split_text(text, max_tokens=48, overlap_tokens=8)

    assert len(chunks) > 1
    assert all(count_tokens(chunk) <= 48 for chunk in chunks)


def test_document_chunks_do_not_cross_sections_and_have_stable_ids() -> None:
    documents = [
        {
            "content": (
                "4.1 First procedure\nThe NEF shall create a subscription.\n\n"
                "4.2 Second procedure\nThe AF shall delete a subscription."
            ),
            "source": "spec.pdf",
            "path": "spec.pdf",
            "metadata": {"document_type": "pdf", "page": 7},
        }
    ]

    first = split_documents(documents)
    second = split_documents(documents)

    assert {chunk["metadata"]["section"] for chunk in first} == {
        "4.1 First procedure",
        "4.2 Second procedure",
    }
    assert [chunk["metadata"]["chunk_id"] for chunk in first] == [
        chunk["metadata"]["chunk_id"] for chunk in second
    ]
    assert all(chunk["metadata"]["page_start"] == 7 for chunk in first)
    assert all(chunk["metadata"]["token_count"] <= 112 for chunk in first)


def test_section_is_carried_to_the_next_pdf_page() -> None:
    documents = [
        {
            "content": "5.1 Monitoring\nThe NEF receives a request.",
            "source": "spec.pdf",
            "path": "spec.pdf",
            "metadata": {"document_type": "pdf", "page": 10},
        },
        {
            "content": "The NEF sends the response.",
            "source": "spec.pdf",
            "path": "spec.pdf",
            "metadata": {"document_type": "pdf", "page": 11},
        },
    ]

    chunks = split_documents(documents)

    assert chunks[-1]["metadata"]["section"] == "5.1 Monitoring"
    assert chunks[-1]["metadata"]["page_start"] == 11
