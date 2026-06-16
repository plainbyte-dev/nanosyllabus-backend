from __future__ import annotations

import logging
import os
import qrcode
from io import BytesIO
import base64
from uuid import uuid4
from fastapi.responses import StreamingResponse
import io
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_student_id, get_current_teacher_id
from app.core.database import get_db
from app.models.document import Document
from app.models.notebook import Notebook
from app.models.User import User, RoleEnum
from app.schemas.notebook import (
    ChatRequest, ChatResponse, DocumentOut,
    NotebookCreate, NotebookOut, NotebookSummary, NotebookUpdate, SourceChunk,
)
from app.services import parser, rag

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notebooks", tags=["notebooks"])
student_router = APIRouter(prefix="/student", tags=["student"])
# FIX: changed prefix from "/notebooks" to "/public/notebooks" to avoid
# route conflicts with the teacher router (both had GET /{notebook_id}).
public_router = APIRouter(prefix="/public/notebooks", tags=["public"])

# ── QR helpers ────────────────────────────────────────────────────────────────

def _build_student_url(teacher_id: str, notebook_id: str) -> str:
    frontend_base = os.getenv("FRONTEND_URL", "http://localhost:3000")
    # Frontend route stays /download/{notebook_id} — unchanged.
    return f"{frontend_base}/download/{notebook_id}"


def generate_qr_base64(url: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def _maybe_generate_qr(nb: Notebook) -> None:
    """Generate QR in-place if notebook is published but QR is missing."""
    if nb.published and not nb.qr_code:
        url = _build_student_url(str(nb.teacher_id), str(nb.id))
        nb.qr_url = url
        nb.qr_code = generate_qr_base64(url)
        logger.info("Auto-generated QR for notebook %s", nb.id)


# ── Serialisation helpers ─────────────────────────────────────────────────────

def doc_out(d: Document) -> DocumentOut:
    return DocumentOut(
        id=str(d.id),
        notebook_id=str(d.notebook_id),
        chapter_title=d.chapter_title,
        original_filename=d.original_filename,
        file_type=d.file_type,
        page_count=d.page_count,
        char_count=d.char_count,
        rag_status=d.rag_status,
        rag_chunk_count=d.rag_chunk_count,
        created_at=d.created_at,
    )


def nb_out(nb: Notebook) -> NotebookOut:
    return NotebookOut(
        id=str(nb.id),
        teacher_id=str(nb.teacher_id),
        title=nb.title,
        subject=nb.subject,
        description=nb.description,
        difficulty=nb.difficulty,
        is_free=nb.is_free,
        published=nb.published,
        student_count=nb.student_count,
        views=nb.views,
        rating=nb.rating,
        rating_count=nb.rating_count,
        qr_code=nb.qr_code,
        qr_url=nb.qr_url,
        created_at=nb.created_at,
        updated_at=nb.updated_at,
        documents=[doc_out(d) for d in (nb.documents or [])],
    )


def nb_summary(nb: Notebook, doc_count: int) -> NotebookSummary:
    return NotebookSummary(
        id=str(nb.id),
        teacher_id=str(nb.teacher_id),
        title=nb.title,
        subject=nb.subject,
        description=nb.description,
        difficulty=nb.difficulty,
        is_free=nb.is_free,
        published=nb.published,
        student_count=nb.student_count,
        views=nb.views,
        rating=nb.rating,
        doc_count=doc_count,
        qr_code=nb.qr_code,
        qr_url=nb.qr_url,
        updated_at=nb.updated_at,
    )


# ── Shared helper ─────────────────────────────────────────────────────────────

async def _get_notebook_or_404(
    notebook_id: str,
    teacher_id: str,
    db: AsyncSession,
    load_docs: bool = False,
) -> Notebook:
    q = select(Notebook).where(
        Notebook.id == notebook_id,
        Notebook.teacher_id == teacher_id,
    )
    if load_docs:
        q = q.options(selectinload(Notebook.documents))
    result = await db.execute(q)
    nb = result.scalar_one_or_none()
    if not nb:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return nb


# ── Teacher notebook endpoints ────────────────────────────────────────────────

@router.post("/", response_model=NotebookOut, status_code=status.HTTP_201_CREATED)
async def create_notebook(
    body: NotebookCreate,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    nb = Notebook(
        teacher_id=teacher_id,
        title=body.title,
        subject=body.subject,
        description=body.description,
        difficulty=body.difficulty,
        is_free=body.is_free,
    )
    db.add(nb)
    await db.commit()
    await db.refresh(nb, ["documents"])
    return nb_out(nb)


@router.get("/", response_model=list[NotebookSummary])
async def list_notebooks(
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notebook, func.count(Document.id).label("doc_count"))
        .outerjoin(Document, Document.notebook_id == Notebook.id)
        .where(Notebook.teacher_id == teacher_id)
        .group_by(Notebook.id)
        .order_by(Notebook.updated_at.desc())
    )
    rows = result.all()

    needs_commit = False
    for nb, _ in rows:
        if nb.published and not nb.qr_code:
            _maybe_generate_qr(nb)
            needs_commit = True

    if needs_commit:
        await db.commit()
        result = await db.execute(
            select(Notebook, func.count(Document.id).label("doc_count"))
            .outerjoin(Document, Document.notebook_id == Notebook.id)
            .where(Notebook.teacher_id == teacher_id)
            .group_by(Notebook.id)
            .order_by(Notebook.updated_at.desc())
        )
        rows = result.all()

    return [nb_summary(nb, doc_count) for nb, doc_count in rows]


@router.get("/{notebook_id}", response_model=NotebookOut)
async def get_notebook(
    notebook_id: str,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    nb = await _get_notebook_or_404(notebook_id, teacher_id, db, load_docs=True)
    return nb_out(nb)


@router.patch("/{notebook_id}", response_model=NotebookOut)
async def update_notebook(
    notebook_id: str,
    body: NotebookUpdate,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    nb = await _get_notebook_or_404(notebook_id, teacher_id, db, load_docs=True)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(nb, field, value)
    await db.commit()
    await db.refresh(nb, ["documents"])
    return nb_out(nb)


@router.delete("/{notebook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notebook(
    notebook_id: str,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    nb = await _get_notebook_or_404(notebook_id, teacher_id, db)
    await db.delete(nb)
    try:
        await rag.delete_notebook(notebook_id)
    except Exception:
        logger.warning("Failed to delete Pinecone namespace %s", notebook_id)
    await db.commit()


@router.patch("/{notebook_id}/publish", response_model=NotebookOut)
async def toggle_publish(
    notebook_id: str,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    nb = await _get_notebook_or_404(notebook_id, teacher_id, db, load_docs=True)
    nb.published = not nb.published

    if nb.published:
        # Generate QR on publish
        url = _build_student_url(teacher_id, notebook_id)
        nb.qr_url = url
        nb.qr_code = generate_qr_base64(url)
        logger.info("QR generated for notebook %s → %s", notebook_id, url)
    else:
        # FIX: clear QR when unpublished so it won't show stale data on the frontend
        nb.qr_url = None
        nb.qr_code = None
        logger.info("QR cleared for unpublished notebook %s", notebook_id)

    await db.commit()
    await db.refresh(nb, ["documents"])
    return nb_out(nb)


@router.post("/{notebook_id}/regenerate-qr", response_model=NotebookOut)
async def regenerate_qr(
    notebook_id: str,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    nb = await _get_notebook_or_404(notebook_id, teacher_id, db, load_docs=True)
    url = _build_student_url(teacher_id, notebook_id)
    nb.qr_url = url
    nb.qr_code = generate_qr_base64(url)
    await db.commit()
    await db.refresh(nb, ["documents"])
    return nb_out(nb)


# ── Document endpoints ────────────────────────────────────────────────────────

MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB

@router.post(
    "/{notebook_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    notebook_id: str,
    file: UploadFile = File(...),
    chapter_title: str = Form(...),
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    await _get_notebook_or_404(notebook_id, teacher_id, db)

    raw_bytes = await file.read(MAX_FILE_BYTES + 1)
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_FILE_BYTES // (1024*1024)} MB limit.",
        )

    file.file = BytesIO(raw_bytes)

    raw_text, file_type, page_count = await parser.extract_text(file)

    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="Document appears to be empty or unreadable.")

    safe_filename = file.filename.strip() or f"upload_{uuid4().hex[:8]}"

    doc = Document(
        notebook_id=notebook_id,
        chapter_title=chapter_title,
        original_filename=safe_filename,
        file_type=file_type,
        page_count=page_count,
        char_count=len(raw_text),
        raw_text=raw_text,
        rag_status="pending",
    )
    db.add(doc)
    await db.flush()

    rag_failed = False
    try:
        chunk_count = await rag.embed_and_index(
            document_id=doc.id,
            notebook_id=notebook_id,
            chapter_title=chapter_title,
            text=raw_text,
        )
        doc.rag_status = "indexed"
        doc.rag_chunk_count = chunk_count
    except Exception:
        logger.exception("RAG indexing failed for doc %s", doc.id)
        doc.rag_status = "failed"
        rag_failed = True

    await db.commit()
    await db.refresh(doc)

    if rag_failed:
        return JSONResponse(
            status_code=status.HTTP_207_MULTI_STATUS,
            content=doc_out(doc).model_dump(),
        )

    return doc_out(doc)


@router.delete(
    "/{notebook_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    notebook_id: str,
    document_id: str,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    await _get_notebook_or_404(notebook_id, teacher_id, db)

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.notebook_id == notebook_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    await db.delete(doc)
    try:
        await rag.delete_document(document_id, notebook_id)
    except Exception:
        logger.warning("Failed to delete Pinecone vectors for doc %s", document_id)
    await db.commit()


# ── Chat endpoints ────────────────────────────────────────────────────────────

@router.post("/{notebook_id}/chat", response_model=ChatResponse)
async def chat_with_notebook(
    notebook_id: str,
    body: ChatRequest,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    await _get_notebook_or_404(notebook_id, teacher_id, db)
    chunks = await rag.query(notebook_id=notebook_id, question=body.question, top_k=body.top_k)
    if not chunks:
        return ChatResponse(answer="No relevant content found in this notebook for your question.", sources=[])
    return ChatResponse(
        answer=await rag.generate_answer(body.question, chunks),
        sources=[SourceChunk(document_id=c["document_id"], chapter_title=c["chapter_title"], text=c["text"], score=c["score"]) for c in chunks],
    )


# ── Student endpoints ─────────────────────────────────────────────────────────

@student_router.get("/teachers")
async def list_published_teachers(
    _: str = Depends(get_current_student_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User, func.count(Notebook.id).label("notebook_count"))
        .join(Notebook, Notebook.teacher_id == User.id)
        .where(User.role == "teacher", Notebook.published.is_(True))
        .group_by(User.id)
        .order_by(User.name.asc())
    )
    return [
        {"id": str(t.id), "name": t.name or t.email, "email": t.email, "picture": t.picture, "notebook_count": nc}
        for t, nc in result.all()
    ]


@student_router.get("/teachers/{teacher_id}/notebooks", response_model=list[NotebookSummary])
async def list_teacher_published_notebooks(
    teacher_id: str,
    _: str = Depends(get_current_student_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notebook, func.count(Document.id).label("doc_count"))
        .outerjoin(Document, Document.notebook_id == Notebook.id)
        .where(Notebook.teacher_id == teacher_id, Notebook.published.is_(True))
        .group_by(Notebook.id)
        .order_by(Notebook.updated_at.desc())
    )
    return [nb_summary(nb, doc_count) for nb, doc_count in result.all()]


@student_router.get("/notebooks/{notebook_id}", response_model=NotebookOut)
async def get_published_notebook(
    notebook_id: str,
    _: str = Depends(get_current_student_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notebook).options(selectinload(Notebook.documents))
        .where(Notebook.id == notebook_id, Notebook.published.is_(True))
    )
    nb = result.scalar_one_or_none()
    if not nb:
        raise HTTPException(status_code=404, detail="Notebook not found")
    nb.views += 1
    await db.commit()
    await db.refresh(nb, ["documents"])
    return nb_out(nb)


@student_router.post("/notebooks/{notebook_id}/chat", response_model=ChatResponse)
async def student_chat_with_notebook(
    notebook_id: str,
    body: ChatRequest,
    _: str = Depends(get_current_student_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notebook).where(Notebook.id == notebook_id, Notebook.published.is_(True))
    )
    nb = result.scalar_one_or_none()
    if not nb:
        raise HTTPException(status_code=404, detail="Notebook not found")
    chunks = await rag.query(notebook_id=notebook_id, question=body.question, top_k=body.top_k)
    if not chunks:
        return ChatResponse(answer="I could not find relevant content in this notebook for that question.", sources=[])
    return ChatResponse(
        answer=await rag.generate_answer(body.question, chunks),
        sources=[SourceChunk(document_id=c["document_id"], chapter_title=c["chapter_title"], text=c["text"], score=c["score"]) for c in chunks],
    )


@student_router.get("/teachers/{teacher_id}")
async def get_teacher_profile(
    teacher_id: str,
    _: str = Depends(get_current_student_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.id == teacher_id, User.role == RoleEnum.teacher)
    )
    teacher = result.scalar_one_or_none()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    nb_result = await db.execute(
        select(func.count(Notebook.id)).where(Notebook.teacher_id == teacher_id, Notebook.published.is_(True))
    )
    return {
        "id": str(teacher.id),
        "name": teacher.name or teacher.email,
        "email": teacher.email,
        "picture": teacher.picture,
        "notebook_count": nb_result.scalar_one(),
    }


# ── Public endpoints (no auth — accessible via QR scan) ──────────────────────

@public_router.get("/{notebook_id}/download")
async def download_notebook_pdf(
    notebook_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — no auth required, accessible via QR scan."""
    result = await db.execute(
        select(Notebook).options(selectinload(Notebook.documents))
        .where(Notebook.id == notebook_id, Notebook.published.is_(True))
    )
    nb = result.scalar_one_or_none()
    if not nb:
        raise HTTPException(status_code=404, detail="Notebook not found")

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
        from reportlab.lib.enums import TA_LEFT
    except ImportError:
        raise HTTPException(status_code=500, detail="PDF generation unavailable. Install reportlab.")

    buf = io.BytesIO()
    pdf_doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("nb_title", parent=styles["Title"], fontSize=22, spaceAfter=12)
    heading_style = ParagraphStyle("nb_heading", parent=styles["Heading1"], fontSize=14, spaceAfter=8)
    body_style = ParagraphStyle("nb_body", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=6)

    story = []

    # Cover page
    story.append(Paragraph(nb.title or "Untitled", title_style))
    story.append(Paragraph(f"Subject: {nb.subject or '—'} · Difficulty: {nb.difficulty or '—'}", body_style))
    # FIX: guard against None description to avoid reportlab crash
    if nb.description:
        story.append(Paragraph(nb.description, body_style))
    story.append(Spacer(1, 0.5 * cm))

    for doc_item in sorted(nb.documents, key=lambda d: d.created_at):
        story.append(PageBreak())
        story.append(Paragraph(doc_item.chapter_title or "Untitled Chapter", heading_style))
        story.append(Paragraph(f"({doc_item.original_filename})", body_style))
        story.append(Spacer(1, 0.3 * cm))

        if doc_item.raw_text:
            for para in doc_item.raw_text.split("\n\n"):
                para = para.strip()
                if para:
                    # Escape XML special chars for reportlab
                    para = (
                        para.replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;")
                    )
                    story.append(Paragraph(para, body_style))
        else:
            story.append(Paragraph("(No text content available)", body_style))

    pdf_doc.build(story)
    buf.seek(0)

    safe_title = (nb.title or "notebook").replace(" ", "_").replace("/", "-")[:50]
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.pdf"'},
    )


@public_router.get("/{notebook_id}/meta")
async def get_notebook_meta(
    notebook_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notebook).where(Notebook.id == notebook_id, Notebook.published.is_(True))
    )
    nb = result.scalar_one_or_none()
    if not nb:
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": str(nb.id), "title": nb.title, "subject": nb.subject}