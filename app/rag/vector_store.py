from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import settings
from app.rag.embedding import EmbeddingModel


_MEMORY_RECORDS: dict[str, dict[str, Any]] = {}
ProgressCallback = Callable[[int, int], None]


class VectorStore:
    """Chroma 优先；缺少 chromadb 时降级到本地 JSON，便于 MVP 无依赖演示。"""

    def __init__(self, embedding_model: EmbeddingModel | None = None) -> None:
        self.embedding_model = embedding_model or EmbeddingModel()
        self.persist_dir = settings.chroma_dir
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = settings.collection_name
        self.backend = "json"
        self.load_error: str | None = None
        self._client = None
        self._collection = None
        self._json_path = self.persist_dir / "fallback_store.json"
        try:
            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
            import chromadb
            from chromadb.config import Settings

            self._client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=Settings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(self.collection_name)
            self.backend = "chroma"
        except Exception as exc:
            self._client = None
            self._collection = None
            self.load_error = str(exc)

    def add_documents(
        self,
        chunks: list[dict[str, Any]],
        batch_size: int = 64,
        progress_callback: ProgressCallback | None = None,
    ) -> int:
        if not chunks:
            return 0
        batch_size = max(1, batch_size)

        if self._collection is not None:
            for done, batch in self._iter_batches(chunks, batch_size):
                texts = [str(chunk["content"]) for chunk in batch]
                embeddings = self.embedding_model.embed(texts)
                ids = [self._doc_id(chunk) for chunk in batch]
                metadatas = [dict(chunk.get("metadata", {})) for chunk in batch]
                for doc_id, embedding, text, metadata in zip(ids, embeddings, texts, metadatas):
                    self._collection.upsert(
                        ids=[doc_id],
                        embeddings=[embedding],
                        documents=[text],
                        metadatas=[metadata],
                    )
                if progress_callback is not None:
                    progress_callback(done, len(chunks))
            return len(chunks)

        global _MEMORY_RECORDS
        records = {record["id"]: record for record in self._load_json_records()}
        for done, batch in self._iter_batches(chunks, batch_size):
            texts = [str(chunk["content"]) for chunk in batch]
            embeddings = self.embedding_model.embed(texts)
            ids = [self._doc_id(chunk) for chunk in batch]
            metadatas = [dict(chunk.get("metadata", {})) for chunk in batch]
            for doc_id, text, embedding, metadata in zip(ids, texts, embeddings, metadatas):
                records[doc_id] = {
                    "id": doc_id,
                    "content": text,
                    "embedding": embedding,
                    "metadata": metadata,
                }
            if progress_callback is not None:
                progress_callback(done, len(chunks))
        try:
            self._json_path.write_text(
                json.dumps(list(records.values()), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except PermissionError:
            # 某些受限运行环境不允许 Python 写文件；同进程内存兜底保证 API 可跑通。
            _MEMORY_RECORDS = records
        return len(chunks)

    def similarity_search(self, query: str, top_k: int = 6) -> list[dict[str, Any]]:
        query_embedding = self.embedding_model.embed_query(query)
        if self._collection is not None:
            result = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )
            docs: list[dict[str, Any]] = []
            documents = result.get("documents", [[]])[0]
            metadatas = result.get("metadatas", [[]])[0]
            distances = result.get("distances", [[]])[0]
            for content, metadata, distance in zip(documents, metadatas, distances):
                score = 1.0 / (1.0 + float(distance))
                docs.append(
                    {
                        "content": content,
                        "source": metadata.get("source", "unknown") if metadata else "unknown",
                        "score": score,
                        "metadata": metadata or {},
                    }
                )
            return docs

        scored: list[dict[str, Any]] = []
        for record in self._load_json_records():
            score = self._cosine(query_embedding, record["embedding"])
            metadata = record.get("metadata", {})
            scored.append(
                {
                    "content": record["content"],
                    "source": metadata.get("source", "unknown"),
                    "score": score,
                    "metadata": metadata,
                }
            )
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k]

    def all_documents(self) -> list[dict[str, Any]]:
        if self._collection is not None:
            result = self._collection.get(include=["documents", "metadatas"])
            docs: list[dict[str, Any]] = []
            for content, metadata in zip(result.get("documents", []), result.get("metadatas", [])):
                metadata = metadata or {}
                docs.append(
                    {
                        "content": content,
                        "source": metadata.get("source", "unknown"),
                        "score": 0.0,
                        "metadata": metadata,
                    }
                )
            return docs
        return [
            {
                "content": record["content"],
                "source": record.get("metadata", {}).get("source", "unknown"),
                "score": 0.0,
                "metadata": record.get("metadata", {}),
            }
            for record in self._load_json_records()
        ]

    def _load_json_records(self) -> list[dict[str, Any]]:
        if not self._json_path.exists():
            return list(_MEMORY_RECORDS.values())
        try:
            return json.loads(self._json_path.read_text(encoding="utf-8"))
        except PermissionError:
            return list(_MEMORY_RECORDS.values())

    def _iter_batches(
        self, chunks: list[dict[str, Any]], batch_size: int
    ) -> list[tuple[int, list[dict[str, Any]]]]:
        batches = []
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            batches.append((start + len(batch), batch))
        return batches

    def _doc_id(self, chunk: dict[str, Any]) -> str:
        metadata = chunk.get("metadata", {})
        source = metadata.get("source") or chunk.get("source", "unknown")
        chunk_index = metadata.get("chunk_index", "")
        content = str(chunk.get("content", ""))
        digest = hashlib.sha1(f"{source}:{chunk_index}:{content}".encode("utf-8")).hexdigest()
        return digest

    def _cosine(self, left: list[float], right: list[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
        right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
        return numerator / (left_norm * right_norm)
