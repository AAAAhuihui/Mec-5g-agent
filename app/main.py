from __future__ import annotations

from fastapi import FastAPI

from app.config import settings
from app.routers import chat, cmd, files, ingest, sessions


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="基于 DeepSeek、混合检索与 ReAct 的 MEC/5G 知识问答和 Kubernetes 辅助诊断系统",
)

app.include_router(ingest.router)
app.include_router(chat.router)
app.include_router(cmd.router)
app.include_router(files.router)
app.include_router(sessions.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
