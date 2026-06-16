from __future__ import annotations

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


# ── Shared base ───────────────────────────────────────────────────────────────

class OrmBase(BaseModel):
    """from_attributes=True causes Pydantic v2 to coerce UUID → str automatically."""
    model_config = {"from_attributes": True}


# ── Document schemas ──────────────────────────────────────────────────────────

class DocumentOut(OrmBase):
    id: str
    notebook_id: str
    chapter_title: str
    original_filename: str
    file_type: str
    page_count: int
    char_count: int
    rag_status: str
    rag_chunk_count: int
    created_at: datetime


# ── Notebook schemas ──────────────────────────────────────────────────────────

Subject = Literal[
    "Mathematics", "Physics", "Chemistry", "Biology",
    "Computer Science", "Economics", "History", "Literature",
]
Difficulty = Literal["Beginner", "Intermediate", "Advanced"]


class NotebookCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=500)
    subject: Subject
    description: str = Field(..., min_length=10, max_length=2000)
    difficulty: Difficulty = "Beginner"
    is_free: bool = False


class NotebookUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=500)
    subject: Optional[Subject] = None
    description: Optional[str] = Field(None, min_length=10, max_length=2000)
    difficulty: Optional[Difficulty] = None
    is_free: Optional[bool] = None
    published: Optional[bool] = None


class NotebookOut(OrmBase):
    id: str
    teacher_id: str
    title: str
    subject: str
    description: str
    difficulty: str
    is_free: bool
    published: bool
    student_count: int
    views: int
    rating: float
    rating_count: int

    qr_code: Optional[str] = None
    qr_url: Optional[str] = None

    created_at: datetime
    updated_at: datetime
    documents: list[DocumentOut] = []
    
    
class NotebookSummary(OrmBase):
    id: str
    teacher_id: str
    title: str
    subject: str
    description: str
    difficulty: str
    is_free: bool
    published: bool
    student_count: int
    views: int
    rating: float
    doc_count: int

    qr_code: Optional[str] = None
    qr_url: Optional[str] = None

    updated_at: datetime


# ── RAG / Chat schemas ────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)
    top_k: int = Field(5, ge=1, le=20)


class SourceChunk(BaseModel):
    document_id: str
    chapter_title: str
    text: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceChunk]