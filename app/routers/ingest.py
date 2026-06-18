from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.rag.document_loader import load_documents
from app.rag.text_splitter import split_documents
from app.rag.vector_store import VectorStore
from app.schemas import IngestRequest, IngestResponse


router = APIRouter(tags=["ingest"])


@router.post("/ingest", response_model=IngestResponse)
def ingest_documents(request: IngestRequest) -> IngestResponse:
    try:
        documents = load_documents(request.source_dir)
        chunks = split_documents(documents)
        stored = VectorStore().add_documents(chunks)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ingest failed: {exc}") from exc

    return IngestResponse(
        success=True,
        message=f"ingested {len(documents)} documents",
        chunks=stored,
    )

