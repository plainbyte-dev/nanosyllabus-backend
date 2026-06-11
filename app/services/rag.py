"""
RAG service:
  - embed_and_index(document_id, notebook_id, chapter_title, text)
      chunks text → Gemini embeddings → upserts to Pinecone
  - query(notebook_id, question, top_k)
      embeds question → queries Pinecone → returns ranked chunks
  - delete_document(document_id)
      deletes all vectors for a document from Pinecone
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import google.generativeai as genai
from pinecone import Pinecone, ServerlessSpec

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Gemini config ─────────────────────────────────────────────────────────────
genai.configure(api_key=settings.GEMINI_API_KEY)
EMBED_MODEL = "models/text-embedding-004"  # 768-dim, free tier available
EMBED_DIM = 768

# ── Pinecone client (lazy-init) ───────────────────────────────────────────────
_pc: Pinecone | None = None
_index: Any = None


def _get_index():
    global _pc, _index
    if _index is not None:
        return _index

    _pc = Pinecone(api_key=settings.PINECONE_API_KEY)
    name = settings.PINECONE_INDEX_NAME

    existing = [i.name for i in _pc.list_indexes()]
    if name not in existing:
        _pc.create_index(
            name=name,
            dimension=EMBED_DIM,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        logger.info("Created Pinecone index: %s", name)

    _index = _pc.Index(name)
    return _index


# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
    """Split text into overlapping word-level chunks."""
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i: i + chunk_size])
        if chunk.strip():
            chunks.append(chunk.strip())
        i += chunk_size - overlap
    return chunks


# ── Embedding ─────────────────────────────────────────────────────────────────

def _embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a batch of texts using Gemini text-embedding-004.
    Gemini supports up to 100 texts per batch.
    """
    all_embeddings: list[list[float]] = []
    batch_size = 50

    for i in range(0, len(texts), batch_size):
        batch = texts[i: i + batch_size]
        result = genai.embed_content(
            model=EMBED_MODEL,
            content=batch,
            task_type="retrieval_document",
        )
        all_embeddings.extend(result["embedding"])

    return all_embeddings


def _embed_query(text: str) -> list[float]:
    result = genai.embed_content(
        model=EMBED_MODEL,
        content=text,
        task_type="retrieval_query",
    )
    return result["embedding"]


# ── Public API ────────────────────────────────────────────────────────────────

async def embed_and_index(
    document_id: str,
    notebook_id: str,
    chapter_title: str,
    text: str,
) -> int:
    """
    Chunk → embed → upsert into Pinecone.
    Returns number of chunks indexed.
    Runs embedding in a thread to avoid blocking the event loop.
    """
    index = _get_index()
    chunks = _chunk_text(text)
    if not chunks:
        return 0

    # Embed in thread (CPU/network-bound)
    embeddings = await asyncio.get_event_loop().run_in_executor(
        None, _embed_texts, chunks
    )

    vectors = [
        {
            "id": f"{document_id}__chunk__{i}",
            "values": emb,
            "metadata": {
                "document_id": document_id,
                "notebook_id": notebook_id,
                "chapter_title": chapter_title,
                "chunk_index": i,
                "text": chunk[:1000],  
            },
        }
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
    ]

    # Upsert in batches of 100
    batch_size = 100
    for i in range(0, len(vectors), batch_size):
        index.upsert(vectors=vectors[i: i + batch_size], namespace=notebook_id)

    logger.info(
        "Indexed %d chunks for document %s in notebook %s",
        len(chunks), document_id, notebook_id,
    )
    return len(chunks)


async def query(
    notebook_id: str,
    question: str,
    top_k: int = 5,
) -> list[dict]:
    """
    Embed question → query Pinecone namespace → return ranked results.
    Each result: {document_id, chapter_title, text, score}
    """
    index = _get_index()

    question_embedding = await asyncio.get_event_loop().run_in_executor(
        None, _embed_query, question
    )

    results = index.query(
        vector=question_embedding,
        top_k=top_k,
        namespace=notebook_id,
        include_metadata=True,
    )

    return [
        {
            "document_id": m.metadata.get("document_id", ""),
            "chapter_title": m.metadata.get("chapter_title", ""),
            "text": m.metadata.get("text", ""),
            "score": round(m.score, 4),
        }
        for m in results.matches
    ]


async def generate_answer(question: str, chunks: list[dict]) -> str:
    """Generate a grounded answer from retrieved notebook chunks."""
    context = "\n\n---\n\n".join(
        f"Source {i + 1} - {c['chapter_title']}:\n{c['text']}"
        for i, c in enumerate(chunks)
    )
    prompt = f"""
You are a teacher's AI clone helping a student.
Answer only from the provided notebook context. If the context is not enough,
say that the notebook does not contain enough information and suggest what to review.
Be clear, step-by-step, and concise.

Notebook context:
{context}

Student question:
{question}
""".strip()

    def _generate() -> str:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        return response.text or "I could not generate an answer from the retrieved notebook context."

    return await asyncio.get_event_loop().run_in_executor(None, _generate)


async def delete_document(document_id: str, notebook_id: str) -> None:
    """Delete all vectors belonging to a document."""
    index = _get_index()

    # Pinecone serverless: delete by metadata filter
    index.delete(
        filter={"document_id": {"$eq": document_id}},
        namespace=notebook_id,
    )
    logger.info("Deleted vectors for document %s", document_id)


async def delete_notebook(notebook_id: str) -> None:
    """Delete entire namespace (all documents in a notebook)."""
    index = _get_index()
    index.delete(delete_all=True, namespace=notebook_id)
    logger.info("Deleted all vectors for notebook %s", notebook_id)
