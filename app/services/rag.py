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
from dotenv import load_dotenv
import asyncio
import logging
from typing import Any
from google import genai
from google.genai import types
from pinecone import Pinecone, ServerlessSpec
load_dotenv()
from app.core.config import settings
import os
logger = logging.getLogger(__name__)

# ── Gemini config ─────────────────────────────────────────────────────────────
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

EMBED_MODEL = "gemini-embedding-001"  # no "models/" prefix needed
EMBED_DIM = 3072

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

    # Create index if it doesn't exist
    if name not in existing:
        logger.info("Creating Pinecone index '%s'...", name)

        _pc.create_index(
            name=name,
            dimension=EMBED_DIM,
            metric="cosine",
            spec=ServerlessSpec(
                cloud="aws",
                region="us-east-1",
            ),
        )

    # Inspect existing index
    desc = _pc.describe_index(name)
    logger.info("Pinecone index description: %s", desc)

    # Validate dimension if available
    if hasattr(desc, "dimension"):
        if desc.dimension != EMBED_DIM:
            raise RuntimeError(
                f"Pinecone index dimension mismatch. "
                f"Expected {EMBED_DIM}, got {desc.dimension}. "
                f"Delete and recreate the index."
            )

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
    all_embeddings: list[list[float]] = []
    batch_size = 50
    for i in range(0, len(texts), batch_size):
        batch = texts[i: i + batch_size]
        result = _client.models.embed_content(
            model=EMBED_MODEL,
            contents=batch,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        all_embeddings.extend([e.values for e in result.embeddings])
    return all_embeddings


def _embed_query(text: str) -> list[float]:
    result = _client.models.embed_content(
        model=EMBED_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    return result.embeddings[0].values


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
    context = "\n\n---\n\n".join(
        f"Source {i + 1} - {c['chapter_title']}:\n{c['text']}"
        for i, c in enumerate(chunks)
    )
    prompt = f"""You are a teacher's AI clone helping a student.
Answer only from the provided notebook context. If the context is not enough,
say that the notebook does not contain enough information and suggest what to review.
Be clear, step-by-step, and concise.

Notebook context:
{context}

Student question:
{question}""".strip()

    def _generate() -> str:
        response = _client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
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
