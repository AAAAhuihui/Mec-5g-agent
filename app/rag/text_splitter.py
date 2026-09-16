from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config import settings


SECTION_PATTERN = re.compile(
    r"^(?P<number>(?:\d+(?:\.\d+)*(?:[A-Za-z])?|[A-Z](?:\.\d+)+))\s+(?P<title>\S.*)$"
)
TOC_DOTS_PATTERN = re.compile(r"(?:\.\s*){5,}\d*\s*$")
LIST_PATTERN = re.compile(r"^(?:[-*\u2022]|\(?\d+\)?[.)]|[a-z][.)])\s+", re.IGNORECASE)
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？;；:：])\s+")


@dataclass
class _Segment:
    text: str
    section: str
    page: int | None
    content_type: str


class _TokenCodec:
    def __init__(self) -> None:
        self.tokenizer: Any | None = None
        try:
            from transformers import AutoTokenizer

            allow_download = os.getenv("EMBEDDING_ALLOW_DOWNLOAD", "0").lower() in {
                "1",
                "true",
                "yes",
            }
            self.tokenizer = AutoTokenizer.from_pretrained(
                settings.embedding_model,
                local_files_only=not allow_download,
            )
        except Exception:
            self.tokenizer = None

    def encode(self, text: str) -> list[Any]:
        if self.tokenizer is not None:
            return list(
                self.tokenizer.encode(text, add_special_tokens=False, verbose=False)
            )
        # Conservative fallback: roughly one token per CJK character and one
        # token per Latin word/punctuation mark.
        return re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\w\s]", text)

    def decode(self, tokens: list[Any]) -> str:
        if self.tokenizer is not None:
            return str(
                self.tokenizer.decode(
                    tokens,
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=False,
                )
            ).strip()
        return " ".join(str(token) for token in tokens).strip()


@lru_cache(maxsize=1)
def _token_codec() -> _TokenCodec:
    return _TokenCodec()


def count_tokens(text: str) -> int:
    return len(_token_codec().encode(text))


def legacy_split_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    """The original character splitter, retained only for label migration."""
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


def split_text(
    text: str,
    *,
    max_tokens: int | None = None,
    overlap_tokens: int | None = None,
    section: str = "Document",
) -> list[str]:
    """Split one text by semantic boundaries, constrained by a token budget."""
    maximum = max_tokens or settings.rag_chunk_max_tokens
    overlap = (
        settings.rag_chunk_overlap_tokens if overlap_tokens is None else overlap_tokens
    )
    if maximum < 16:
        raise ValueError("max_tokens must be at least 16")
    if overlap < 0 or overlap >= maximum:
        raise ValueError("overlap_tokens must be in [0, max_tokens)")

    segments, _ = _extract_segments(text, section=section, page=None)
    chunks = _pack_segments(segments, maximum, overlap)
    return [chunk["content"] for chunk in chunks]


def split_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create section-aware, page-aware and token-bounded retrieval chunks."""
    max_tokens = settings.rag_chunk_max_tokens
    overlap_tokens = settings.rag_chunk_overlap_tokens
    chunks: list[dict[str, Any]] = []
    source_indexes: dict[str, int] = {}
    current_sections: dict[str, str] = {}
    identity_occurrences: dict[str, int] = {}

    for document in documents:
        content = str(document.get("content", "")).strip()
        if not content:
            continue
        source = str(document.get("source", "unknown"))
        path = str(document.get("path", ""))
        document_metadata = dict(document.get("metadata", {}))
        page = _optional_int(document_metadata.get("page"))
        initial_section = current_sections.get(source, "Document")
        segments, final_section = _extract_segments(
            content,
            section=initial_section,
            page=page,
        )
        current_sections[source] = final_section

        for packed in _pack_segments(segments, max_tokens, overlap_tokens):
            index = source_indexes.get(source, 0)
            source_indexes[source] = index + 1
            section_name = str(packed["section"])
            body = str(packed["body"])
            page_start = packed.get("page_start")
            page_end = packed.get("page_end")
            parent_id = _stable_digest(source, section_name)
            identity = _stable_digest(
                source,
                str(page_start or ""),
                section_name,
                _normalize_for_id(body),
            )
            occurrence = identity_occurrences.get(identity, 0)
            identity_occurrences[identity] = occurrence + 1
            chunk_id = _stable_digest(identity, str(occurrence))
            metadata: dict[str, Any] = {
                "source": source,
                "path": path,
                "chunk_index": index,
                "chunk_id": chunk_id,
                "parent_id": parent_id,
                "section": section_name,
                "content_type": packed["content_type"],
                "token_count": count_tokens(str(packed["content"])),
                "chunking_version": "section-token-v2",
            }
            if page_start is not None:
                metadata["page_start"] = page_start
                metadata["page_end"] = page_end
            chunks.append(
                {
                    "content": packed["content"],
                    "source": source,
                    "metadata": metadata,
                }
            )
    return chunks


def _extract_segments(
    text: str,
    *,
    section: str,
    page: int | None,
) -> tuple[list[_Segment], str]:
    lines = [line.strip() for line in text.replace("\u00a0", " ").splitlines()]
    segments: list[_Segment] = []
    buffer: list[str] = []
    content_type = "paragraph"
    current_section = section

    def flush() -> None:
        nonlocal buffer, content_type
        value = "\n".join(line for line in buffer if line).strip()
        if value:
            segments.append(_Segment(value, current_section, page, content_type))
        buffer = []
        content_type = "paragraph"

    for line in lines:
        if not line:
            flush()
            continue
        if TOC_DOTS_PATTERN.search(line):
            # Table-of-contents dot leaders contain no retrievable evidence and
            # otherwise create hundreds of punctuation-only token windows.
            flush()
            continue
        heading = _section_heading(line)
        if heading:
            flush()
            current_section = heading
            continue

        detected_type = _content_type(line)
        if buffer and content_type in {"table", "api_schema"}:
            # A table/schema title is followed by rows whose individual lines
            # do not repeat the marker. Keep the full block together until a
            # blank line or section boundary.
            buffer.append(line)
            continue
        if buffer and detected_type != content_type and (
            detected_type in {"table", "procedure", "api_schema"}
            or content_type in {"table", "api_schema"}
        ):
            flush()
        content_type = detected_type if not buffer else content_type
        buffer.append(line)
    flush()
    return segments, current_section


def _section_heading(line: str) -> str | None:
    if TOC_DOTS_PATTERN.search(line) or len(line) > 180:
        return None
    match = SECTION_PATTERN.match(line)
    if not match:
        return None
    number = match.group("number")
    title = match.group("title").strip()
    # Exclude ordinary numbered list items and decimal values.
    if "." not in number and number.isdigit() and int(number) > 30:
        return None
    if "." not in number and not number.isdigit():
        return None
    if title[0] in ".,:;":
        return None
    return f"{number} {title}"


def _content_type(text: str) -> str:
    lowered = text.casefold()
    if re.match(r"^table\s+[a-z0-9.-]+\s*[:\-]", lowered) or text.count("|") >= 2:
        return "table"
    if any(term in lowered for term in ("openapi", "requestbody:", "responses:", "schema:")):
        return "api_schema"
    if LIST_PATTERN.match(text) or any(
        term in lowered for term in (" shall ", "procedure", "the nef shall", "the af shall")
    ):
        return "procedure"
    return "paragraph"


def _pack_segments(
    segments: list[_Segment],
    max_tokens: int,
    overlap_tokens: int,
) -> list[dict[str, Any]]:
    expanded: list[_Segment] = []
    for segment in segments:
        prefix = _section_prefix(segment.section)
        body_budget = max(8, max_tokens - count_tokens(prefix) - 2)
        pieces = _split_body(segment.text, body_budget, overlap_tokens)
        if segment.content_type == "table" and len(pieces) > 1:
            table_title = segment.text.splitlines()[0].strip()
            title_tokens = count_tokens(table_title)
            if title_tokens < body_budget // 2:
                pieces = [
                    piece
                    if index == 0 or piece.startswith(table_title)
                    else _fit_with_prefix(table_title, piece, body_budget)
                    for index, piece in enumerate(pieces)
                ]
        for piece in pieces:
            expanded.append(
                _Segment(piece, segment.section, segment.page, segment.content_type)
            )

    packed: list[dict[str, Any]] = []
    pending: list[_Segment] = []

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        section_name = pending[0].section
        body = "\n\n".join(item.text for item in pending).strip()
        pages = [item.page for item in pending if item.page is not None]
        content_types = {item.content_type for item in pending}
        packed.append(
            {
                "content": f"{_section_prefix(section_name)}\n{body}".strip(),
                "body": body,
                "section": section_name,
                "content_type": (
                    next(iter(content_types)) if len(content_types) == 1 else "mixed"
                ),
                "page_start": min(pages) if pages else None,
                "page_end": max(pages) if pages else None,
            }
        )
        pending = []

    for segment in expanded:
        pending_types = {item.content_type for item in pending}
        compatible_narrative = pending_types.union({segment.content_type}) <= {
            "paragraph",
            "procedure",
        }
        if pending and (
            pending[0].section != segment.section
            or pending[0].page != segment.page
            or (
                pending[0].content_type != segment.content_type
                and not compatible_narrative
            )
        ):
            flush()
        candidate = pending + [segment]
        candidate_text = (
            f"{_section_prefix(segment.section)}\n"
            + "\n\n".join(item.text for item in candidate)
        )
        if pending and count_tokens(candidate_text) > max_tokens:
            flush()
        pending.append(segment)
    flush()
    return packed


def _split_body(text: str, token_budget: int, overlap_tokens: int) -> list[str]:
    codec = _token_codec()
    tokens = codec.encode(text)
    if len(tokens) <= token_budget:
        return [text.strip()]

    # Sentence packing avoids cutting a sentence when the extracted PDF text
    # contains usable punctuation. An individual long sentence falls back to
    # deterministic token windows.
    sentences = [part.strip() for part in SENTENCE_BOUNDARY.split(text) if part.strip()]
    if len(sentences) > 1:
        pieces: list[str] = []
        pending: list[str] = []
        for sentence in sentences:
            if len(codec.encode(sentence)) > token_budget:
                if pending:
                    pieces.append(" ".join(pending))
                    pending = []
                pieces.extend(_token_windows(sentence, token_budget, overlap_tokens))
                continue
            candidate = " ".join(pending + [sentence])
            if pending and len(codec.encode(candidate)) > token_budget:
                pieces.append(" ".join(pending))
                pending = [sentence]
            else:
                pending.append(sentence)
        if pending:
            pieces.append(" ".join(pending))
        return pieces
    return _token_windows(text, token_budget, overlap_tokens)


def _token_windows(text: str, token_budget: int, overlap_tokens: int) -> list[str]:
    codec = _token_codec()
    tokens = codec.encode(text)
    step = max(1, token_budget - min(overlap_tokens, token_budget - 1))
    pieces: list[str] = []
    for start in range(0, len(tokens), step):
        piece = codec.decode(tokens[start : start + token_budget]).strip()
        if piece:
            pieces.append(piece)
        if start + token_budget >= len(tokens):
            break
    return pieces


def _section_prefix(section: str) -> str:
    value = section or "Document"
    codec = _token_codec()
    tokens = codec.encode(value)
    if len(tokens) > 32:
        value = codec.decode(tokens[:32]).strip()
    return f"Section: {value}"


def _fit_with_prefix(prefix: str, text: str, token_budget: int) -> str:
    codec = _token_codec()
    prefix_tokens = codec.encode(prefix)
    remaining = max(1, token_budget - len(prefix_tokens) - 1)
    body = codec.decode(codec.encode(text)[:remaining]).strip()
    return f"{prefix}\n{body}".strip()


def _normalize_for_id(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _stable_digest(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
