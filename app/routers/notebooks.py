from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_teacher_id
from app.core.database import get_db
from app.models.document import Document
from app.models.notebook import Notebook
from app.schemas.notebook import (
    ChatRequest, ChatResponse, DocumentOut,
    NotebookCreate, NotebookOut, NotebookSummary, NotebookUpdate, SourceChunk,
)
from app.services import parser, rag

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notebooks", tags=["notebooks"])


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

    return nb


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
    return [
        NotebookSummary(
            id=nb.id, teacher_id=nb.teacher_id, title=nb.title,
            subject=nb.subject, description=nb.description,
            difficulty=nb.difficulty, is_free=nb.is_free,
            published=nb.published, student_count=nb.student_count,
            views=nb.views, rating=nb.rating, doc_count=doc_count,
            updated_at=nb.updated_at,
        )
        for nb, doc_count in rows
    ]


@router.get("/{notebook_id}", response_model=NotebookOut)
async def get_notebook(
    notebook_id: str,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    return await _get_notebook_or_404(notebook_id, teacher_id, db, load_docs=True)


@router.patch("/{notebook_id}", response_model=NotebookOut)
async def update_notebook(
    notebook_id: str,
    body: NotebookUpdate,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    nb = await _get_notebook_or_404(
        notebook_id,
        teacher_id,
        db,
        load_docs=True,
    )

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(nb, field, value)

    await db.commit()
    await db.refresh(nb)

    return nb


@router.delete("/{notebook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notebook(
    notebook_id: str,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    nb = await _get_notebook_or_404(
        notebook_id,
        teacher_id,
        db,
    )

    await db.delete(nb)

    try:
        await rag.delete_notebook(notebook_id)
    except Exception:
        logger.warning(
            "Failed to delete Pinecone namespace %s",
            notebook_id,
        )

    await db.commit()


@router.patch("/{notebook_id}/publish", response_model=NotebookOut)
async def toggle_publish(
    notebook_id: str,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    nb = await _get_notebook_or_404(
        notebook_id,
        teacher_id,
        db,
        load_docs=True,
    )

    nb.published = not nb.published

    await db.commit()
    await db.refresh(nb)

    return nb


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
    await _get_notebook_or_404(
        notebook_id,
        teacher_id,
        db,
    )

    raw_text, file_type, page_count = await parser.extract_text(file)

    if not raw_text.strip():
        raise HTTPException(
            status_code=422,
            detail="Document appears to be empty or unreadable.",
        )

    doc = Document(
        notebook_id=notebook_id,
        chapter_title=chapter_title,
        original_filename=file.filename or "upload",
        file_type=file_type,
        page_count=page_count,
        char_count=len(raw_text),
        raw_text=raw_text,
        rag_status="processing",
    )

    db.add(doc)

    await db.flush()

    try:
        chunk_count = await rag.embed_and_index(
            document_id=doc.id,
            notebook_id=notebook_id,
            chapter_title=chapter_title,
            text=raw_text,
        )

        doc.rag_status = "indexed"
        doc.rag_chunk_count = chunk_count

    except Exception as e:
        logger.exception(
            "RAG indexing failed for doc %s",
            doc.id,
        )

        doc.rag_status = "failed"

    await db.commit()
    await db.refresh(doc)

    return doc

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
    await _get_notebook_or_404(
        notebook_id,
        teacher_id,
        db,
    )

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.notebook_id == notebook_id,
        )
    )

    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    await db.delete(doc)

    try:
        await rag.delete_document(
            document_id,
            notebook_id,
        )
    except Exception:
        logger.warning(
            "Failed to delete Pinecone vectors for doc %s",
            document_id,
        )

    await db.commit()

@router.post("/{notebook_id}/chat", response_model=ChatResponse)
async def chat_with_notebook(
    notebook_id: str,
    body: ChatRequest,
    teacher_id: str = Depends(get_current_teacher_id),
    db: AsyncSession = Depends(get_db),
):
    await _get_notebook_or_404(notebook_id, teacher_id, db)
    chunks = await rag.query(
        notebook_id=notebook_id,
        question=body.question,
        top_k=body.top_k,
    )
    if not chunks:
        return ChatResponse(
            answer="No relevant content found in this notebook for your question.",
            sources=[],
        )
    context = "\n\n---\n\n".join(
        f"[{c['chapter_title']}]\n{c['text']}" for c in chunks
    )
    return ChatResponse(
        answer=f"Based on the notebook content:\n\n{context}",
        sources=[
            SourceChunk(
                document_id=c["document_id"],
                chapter_title=c["chapter_title"],
                text=c["text"],
                score=c["score"],
            )
            for c in chunks
        ],
    )