import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    notebook_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("notebooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_title: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf | docx | txt
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)

    # RAG status
    rag_status: Mapped[str] = mapped_column(
        SAEnum("pending", "processing", "indexed", "failed", name="rag_status_enum"),
        default="pending",
        nullable=False,
    )
    rag_chunk_count: Mapped[int] = mapped_column(Integer, default=0)

    raw_text: Mapped[str] = mapped_column(Text, nullable=True)  # extracted text stored for re-indexing

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    notebook: Mapped["Notebook"] = relationship("Notebook", back_populates="documents")  # noqa: F821