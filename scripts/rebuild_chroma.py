"""Build a verified Chroma collection from the section/token chunker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402
from app.rag.document_loader import load_documents  # noqa: E402
from app.rag.embedding import EmbeddingModel  # noqa: E402
from app.rag.text_splitter import split_documents  # noqa: E402
from app.rag.vector_store import VectorStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default="data/raw_docs")
    parser.add_argument("--collection", default="mec_5g_docs_structured_v2")
    parser.add_argument("--persist-dir", default=str(settings.chroma_dir))
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--allow-hashing", action="store_true")
    parser.add_argument(
        "--manifest",
        default="data/evals/chroma_structured_v2_manifest.json",
    )
    args = parser.parse_args()

    if args.collection == "mec_5g_docs" and args.replace:
        raise SystemExit("refusing to replace the legacy mec_5g_docs collection")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    if os.name == "nt":
        try:
            str(Path(args.persist_dir)).encode("ascii")
        except UnicodeEncodeError as exc:
            raise SystemExit(
                "On Windows, use an ASCII-only --persist-dir for Chroma/HNSW "
                "persistence (for example D:/mec_5g_agent_data/chroma_db)."
            ) from exc

    if args.replace:
        _delete_exact_collection(args.collection, Path(args.persist_dir))
    store = VectorStore(collection_name=args.collection, persist_dir=args.persist_dir)
    if store.backend != "chroma":
        raise SystemExit(f"Chroma is unavailable: {store.load_error}")
    if store.count() > 0:
        raise SystemExit(
            f"collection {args.collection!r} already contains {store.count()} records; "
            "choose a new name or pass --replace"
        )
    model = store.embedding_model
    if model.backend != "sentence-transformers" and not args.allow_hashing:
        raise SystemExit(
            "sentence-transformers model is unavailable; refusing to persist fallback "
            f"hash embeddings: {model.load_error}"
        )

    documents = load_documents(args.source_dir)
    chunks = split_documents(documents)
    if not chunks:
        raise SystemExit("source directory produced no chunks")
    token_counts = [int(chunk["metadata"]["token_count"]) for chunk in chunks]
    if max(token_counts) > settings.rag_chunk_max_tokens:
        raise SystemExit("chunker produced a record over the configured token limit")
    if len({chunk["metadata"]["chunk_id"] for chunk in chunks}) != len(chunks):
        raise SystemExit("chunk IDs are not unique")

    def progress(done: int, total: int) -> None:
        print(f"embedded {done}/{total}", flush=True)

    stored = store.add_documents(
        chunks,
        batch_size=args.batch_size,
        progress_callback=progress,
    )
    persisted = store.count()
    if stored != len(chunks) or persisted != len(chunks):
        raise SystemExit(
            f"verification failed: chunks={len(chunks)}, stored={stored}, persisted={persisted}"
        )
    probe_results = store.similarity_search("NEF northbound API", top_k=1)
    if not probe_results:
        raise SystemExit("verification failed: the persisted collection returned no probe result")
    hnsw_files = sorted(
        path.name for path in store.persist_dir.glob("*/*") if path.is_file()
    )
    if "index_metadata.pickle" in hnsw_files and "header.bin" not in hnsw_files:
        raise SystemExit(
            "verification failed: HNSW metadata exists but header.bin is missing; "
            "on Windows this is commonly caused by a non-ASCII persistence path"
        )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "collection": args.collection,
        "chroma_dir": str(store.persist_dir),
        "embedding_backend": model.backend,
        "embedding_model": model.model_name,
        "chunking_version": "section-token-v2",
        "chunk_max_tokens": settings.rag_chunk_max_tokens,
        "chunk_overlap_tokens": settings.rag_chunk_overlap_tokens,
        "document_units": len(documents),
        "chunk_count": len(chunks),
        "max_observed_tokens": max(token_counts),
        "content_types": dict(Counter(chunk["metadata"]["content_type"] for chunk in chunks)),
        "source_fingerprint": _source_fingerprint(chunks),
        "verified_collection_count": persisted,
        "verified_probe_results": len(probe_results),
        "hnsw_files": hnsw_files,
    }
    manifest_path = _resolve(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _delete_exact_collection(collection_name: str, persist_dir: Path) -> None:
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=str(persist_dir),
        settings=Settings(anonymized_telemetry=False),
    )
    existing = {
        item.name if hasattr(item, "name") else str(item)
        for item in client.list_collections()
    }
    if collection_name in existing:
        client.delete_collection(collection_name)


def _source_fingerprint(chunks: list[dict]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(str(chunk["metadata"]["chunk_id"]).encode("ascii"))
    return digest.hexdigest()


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
