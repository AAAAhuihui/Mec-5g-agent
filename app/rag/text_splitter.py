from __future__ import annotations


def split_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    if chunk_size <= overlap:
        raise ValueError("chunk_size must be greater than overlap")

    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        window = normalized[start:end]
        # 优先在段落或句号处分片，减少语义截断。
        cut = max(window.rfind("\n\n"), window.rfind("。"), window.rfind(". "))
        if cut > chunk_size * 0.5 and end < len(normalized):
            end = start + cut + 1
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(0, end - overlap)
    return chunks


def split_documents(documents: list[dict[str, str]]) -> list[dict[str, object]]:
    chunks: list[dict[str, object]] = []
    for document in documents:
        for index, chunk in enumerate(split_text(document["content"])):
            chunks.append(
                {
                    "content": chunk,
                    "source": document["source"],
                    "metadata": {
                        "source": document["source"],
                        "path": document["path"],
                        "chunk_index": index,
                    },
                }
            )
    return chunks
