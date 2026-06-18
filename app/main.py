from __future__ import annotations

from fastapi import FastAPI

from app.config import settings
from app.routers import chat, ingest


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="基于 DeepSeek + RAG + Agent 的 MEC/5G 信令智能问答系统 MVP",
)

app.include_router(ingest.router)
app.include_router(chat.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

