from __future__ import annotations

from pathlib import Path


SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".pdf", ".docx"}


def resolve_source_dir(source_dir: str) -> Path:
    path = Path(source_dir)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return path


def load_documents(source_dir: str) -> list[dict[str, str]]:
    root = resolve_source_dir(source_dir)
    if not root.exists():
        raise FileNotFoundError(f"source_dir not found: {root}")

    documents: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            text = load_document_text(path)
            if text.strip():
                documents.append(
                    {
                        "content": text,
                        "source": path.name,
                        "path": str(path),
                    }
                )
    return documents


def load_document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        return _load_pdf(path)
    if suffix == ".docx":
        return _load_docx(path)
    raise ValueError(f"unsupported document type: {path.suffix}")


def _load_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError("读取 PDF 需要安装依赖：pip install pypdf") from exc

    reader = PdfReader(str(path))
    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"# Page {index}\n{text.strip()}")
    return "\n\n".join(pages)


def _load_docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise ImportError("读取 docx 需要安装依赖：pip install python-docx") from exc

    document = Document(str(path))
    parts: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            parts.append(text)

    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)
