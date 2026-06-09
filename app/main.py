import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import create_tables
from app.routers import notebooks, auth, dashboard
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="TeacherOS API",
    description="Notebooks, RAG document indexing, and AI chat powered by Gemini + Pinecone.",
    version="1.0.0",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(notebooks.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")

# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    await create_tables()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "TeacherOS API"}