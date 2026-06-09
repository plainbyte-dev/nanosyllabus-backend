import io
from fastapi import UploadFile, HTTPException


async def extract_text(file: UploadFile) -> tuple[str, str, int]:
    """
    Returns (raw_text, file_type, page_count).
    Raises HTTPException for unsupported types.
    """
    filename = file.filename or ""
    content = await file.read()

    if filename.endswith(".pdf"):
        return await _parse_pdf(content)

    if filename.endswith(".docx"):
        return _parse_docx(content)

    if filename.endswith((".txt", ".md")):
        text = content.decode("utf-8", errors="ignore")
        pages = max(1, len(text) // 3000)
        return text, "txt", pages

    raise HTTPException(
        status_code=415,
        detail=f"Unsupported file type: {filename}. Use PDF, DOCX, or TXT.",
    )


async def _parse_pdf(content: bytes) -> tuple[str, str, int]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = len(reader.pages)
        text = "\n\n".join(
            page.extract_text() or "" for page in reader.pages
        )
        return text.strip(), "pdf", pages
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse PDF: {e}")


def _parse_docx(content: bytes) -> tuple[str, str, int]:
    try:
        from docx import Document

        doc = Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs)
        pages = max(1, len(text) // 3000)
        return text.strip(), "docx", pages
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse DOCX: {e}")