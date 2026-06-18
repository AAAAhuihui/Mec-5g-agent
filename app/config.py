from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


_load_dotenv()


BASE_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    app_name: str = "MEC/5G RAG Agent"
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    embedding_model: str = os.getenv(
        "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    chroma_dir: Path = BASE_DIR / os.getenv("CHROMA_DIR", "data/chroma_db")
    raw_docs_dir: Path = BASE_DIR / os.getenv("RAW_DOCS_DIR", "data/raw_docs")
    collection_name: str = os.getenv("CHROMA_COLLECTION", "mec_5g_docs")
    top_k: int = int(os.getenv("RAG_TOP_K", "6"))


settings = Settings()
