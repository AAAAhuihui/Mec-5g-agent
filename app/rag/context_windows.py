from __future__ import annotations

import hashlib
from typing import Any


def build_context_windows(
    candidates: list[dict[str, Any]],
    corpus: list[dict[str, Any]],
    *,
    radius: int = 1,
) -> list[dict[str, Any]]:
    """Expand each candidate into a same-section neighbour window.

    With the default radius, a window contains the previous chunk, the
    retrieved centre chunk and the next chunk. Missing or cross-section
    neighbours are omitted.
    """
    if radius < 0:
        raise ValueError("radius must be non-negative")
    index: dict[tuple[str, int], dict[str, Any]] = {}
    for document in corpus:
        chunk_index = _chunk_index(document)
        if chunk_index is not None:
            index[(_source(document), chunk_index)] = document

    windows: list[dict[str, Any]] = []
    seen_centres: set[str] = set()
    for candidate in candidates:
        centre_id = _chunk_id(candidate)
        if centre_id in seen_centres:
            continue
        seen_centres.add(centre_id)
        centre_index = _chunk_index(candidate)
        source = _source(candidate)
        section = str(candidate.get("metadata", {}).get("section", ""))
        members: list[dict[str, Any]] = []
        if centre_index is None:
            members = [candidate]
        else:
            for offset in range(-radius, radius + 1):
                member = index.get((source, centre_index + offset))
                if member is None:
                    continue
                member_section = str(member.get("metadata", {}).get("section", ""))
                if member_section != section:
                    continue
                members.append(member)
        if not members:
            members = [candidate]
        members.sort(key=lambda item: _chunk_index(item) or 0)
        windows.append(_make_window(candidate, members))
    return windows


def window_contains_gold(
    window: dict[str, Any],
    *,
    expected_chunk_ids: set[str],
    expected_chunk_indexes: set[int],
) -> bool:
    metadata = window.get("metadata", {})
    if expected_chunk_ids:
        return bool(set(metadata.get("member_chunk_ids", [])) & expected_chunk_ids)
    if expected_chunk_indexes:
        indexes = {
            int(value)
            for value in metadata.get("member_chunk_indexes", [])
            if _as_int(value) is not None
        }
        return bool(indexes & expected_chunk_indexes)
    return False


def _make_window(
    centre: dict[str, Any],
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    member_ids = [_chunk_id(member) for member in members]
    member_indexes = [
        index for member in members if (index := _chunk_index(member)) is not None
    ]
    centre_metadata = dict(centre.get("metadata", {}))
    centre_id = _chunk_id(centre)
    window_id = hashlib.sha1("\x1f".join(member_ids).encode("utf-8")).hexdigest()
    pages = [
        page
        for member in members
        if (page := _as_int(member.get("metadata", {}).get("page_start"))) is not None
    ]
    content_parts = []
    for member in members:
        role = "CENTRE" if _chunk_id(member) == centre_id else "NEIGHBOUR"
        index = _chunk_index(member)
        content_parts.append(
            f"[{role} chunk {index if index is not None else '?'}]\n"
            f"{str(member.get('content', '')).strip()}"
        )
    metadata = {
        **centre_metadata,
        "window_id": window_id,
        "center_chunk_id": centre_id,
        "center_chunk_index": _chunk_index(centre),
        "member_chunk_ids": member_ids,
        "member_chunk_indexes": member_indexes,
        "window_size": len(members),
        "context_role": "window",
    }
    if pages:
        metadata["page_start"] = min(pages)
        metadata["page_end"] = max(pages)
    return {
        **centre,
        "content": "\n\n".join(content_parts),
        "source": _source(centre),
        "metadata": metadata,
        "window_members": members,
    }


def _source(document: dict[str, Any]) -> str:
    return str(
        document.get("metadata", {}).get("source")
        or document.get("source", "unknown")
    )


def _chunk_id(document: dict[str, Any]) -> str:
    value = str(document.get("metadata", {}).get("chunk_id", "")).strip()
    if value:
        return value
    source = _source(document)
    index = _chunk_index(document)
    content = str(document.get("content", ""))
    return hashlib.sha1(f"{source}:{index}:{content}".encode("utf-8")).hexdigest()


def _chunk_index(document: dict[str, Any]) -> int | None:
    return _as_int(document.get("metadata", {}).get("chunk_index"))


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
