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
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    web_search_max_results: int = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))
    k8s_mcp_url: str = os.getenv("K8S_MCP_URL", "http://127.0.0.1:8000/mcp")
    k8s_mcp_timeout_seconds: int = int(os.getenv("K8S_MCP_TIMEOUT_SECONDS", "30"))
    k8s_diagnostics_max_log_lines: int = int(os.getenv("K8S_DIAGNOSTICS_MAX_LOG_LINES", "500"))
    k8s_diagnostics_max_since_seconds: int = int(
        os.getenv("K8S_DIAGNOSTICS_MAX_SINCE_SECONDS", "86400")
    )
    # CMD is deliberately opt-in. The API client must approve the exact pending command first.
    cmd_tool_enabled: bool = os.getenv("CMD_TOOL_ENABLED", "false").lower() == "true"
    cmd_tool_timeout_seconds: int = int(os.getenv("CMD_TOOL_TIMEOUT_SECONDS", "30"))
    cmd_tool_approval_ttl_seconds: int = int(os.getenv("CMD_TOOL_APPROVAL_TTL_SECONDS", "300"))
    cmd_tool_allowed_workdir: Path = BASE_DIR / os.getenv("CMD_TOOL_ALLOWED_WORKDIR", ".")
    cmd_tool_max_output_chars: int = int(os.getenv("CMD_TOOL_MAX_OUTPUT_CHARS", "10000"))
    file_tool_enabled: bool = os.getenv("FILE_TOOL_ENABLED", "true").lower() == "true"
    file_tool_allowed_root: Path = Path(
        os.path.expandvars(os.getenv("FILE_TOOL_ALLOWED_ROOT", "%USERPROFILE%\\Desktop"))
    )
    file_tool_max_read_chars: int = int(os.getenv("FILE_TOOL_MAX_READ_CHARS", "20000"))
    file_tool_max_write_chars: int = int(os.getenv("FILE_TOOL_MAX_WRITE_CHARS", "200000"))
    react_max_steps: int = int(os.getenv("REACT_MAX_STEPS", "6"))
    react_max_tool_calls: int = int(os.getenv("REACT_MAX_TOOL_CALLS", "8"))
    react_max_cmd_approvals: int = int(os.getenv("REACT_MAX_CMD_APPROVALS", "3"))
    react_max_file_approvals: int = int(os.getenv("REACT_MAX_FILE_APPROVALS", "3"))
    # Kept for compatibility with existing .env files; new code uses the clearer name below.
    react_max_duplicate_commands: int = int(os.getenv("REACT_MAX_DUPLICATE_COMMANDS", "2"))
    react_max_same_command_retries: int = int(
        os.getenv("REACT_MAX_SAME_COMMAND_RETRIES", os.getenv("REACT_MAX_DUPLICATE_COMMANDS", "2"))
    )
    react_max_no_progress_steps: int = int(os.getenv("REACT_MAX_NO_PROGRESS_STEPS", "3"))
    embedding_model: str = os.getenv(
        "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    chroma_dir: Path = BASE_DIR / os.getenv("CHROMA_DIR", "data/chroma_db")
    raw_docs_dir: Path = BASE_DIR / os.getenv("RAW_DOCS_DIR", "data/raw_docs")
    collection_name: str = os.getenv("CHROMA_COLLECTION", "mec_5g_docs")
    # MiniLM in this project has a 128-token input window. Keeping chunks at
    # 112 leaves room for special tokens and query/document prefixes.
    rag_chunk_max_tokens: int = int(os.getenv("RAG_CHUNK_MAX_TOKENS", "112"))
    rag_chunk_overlap_tokens: int = int(os.getenv("RAG_CHUNK_OVERLAP_TOKENS", "16"))
    top_k: int = int(os.getenv("RAG_TOP_K", "6"))
    rag_vector_candidate_k: int = int(os.getenv("RAG_VECTOR_CANDIDATE_K", "40"))
    rag_bm25_candidate_k: int = int(os.getenv("RAG_BM25_CANDIDATE_K", "40"))
    rag_fused_candidate_k: int = int(os.getenv("RAG_FUSED_CANDIDATE_K", "30"))
    rag_rrf_k: int = int(os.getenv("RAG_RRF_K", "60"))
    rag_neighbor_radius: int = int(os.getenv("RAG_NEIGHBOR_RADIUS", "1"))
    reranker_backend: str = os.getenv("RERANKER_BACKEND", "lightweight").lower()
    reranker_model: str = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    reranker_device: str = os.getenv("RERANKER_DEVICE", "cpu")
    reranker_batch_size: int = int(os.getenv("RERANKER_BATCH_SIZE", "8"))
    reranker_max_length: int = int(os.getenv("RERANKER_MAX_LENGTH", "512"))
    reranker_allow_download: bool = os.getenv("RERANKER_ALLOW_DOWNLOAD", "false").lower() == "true"
    reranker_fail_open: bool = os.getenv("RERANKER_FAIL_OPEN", "true").lower() == "true"
    mysql_host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "")
    mysql_database: str = os.getenv("MYSQL_DATABASE", "mec_5g_agent")
    # Redis is optional acceleration only; MySQL remains the source of truth.
    redis_host: str = os.getenv("REDIS_HOST", "")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_db: int = int(os.getenv("REDIS_DB", "0"))
    redis_password: str = os.getenv("REDIS_PASSWORD", "")
    redis_context_ttl_seconds: int = int(os.getenv("REDIS_CONTEXT_TTL_SECONDS", "600"))
    redis_socket_timeout_seconds: float = float(os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "1.0"))


settings = Settings()
