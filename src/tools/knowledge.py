"""kb_search(query) — knowledge-base retrieval for C5 (owner: D, content: RAG team).

C5 forbids stating any fact about City rules, fees or services that a tool
did not return; this tool is how such facts reach the agent. Results come
from the Supabase knowledge base (kb_chunks), hybrid full-text + vector when
embeddings are enabled, full-text only otherwise.
"""
from __future__ import annotations

from src.config import app_config
from src.rag.embeddings import embed
from src.tools.store import StoreError, get_store, store_failure


def kb_search(query: str, top_k: int | None = None) -> dict:
    if not query or not query.strip():
        return {"ok": False, "error": "query is required"}
    top_k = top_k or app_config()["retrieval"]["top_k"]
    try:
        vectors = embed([query])
    except Exception as e:  # embeddings are an optional layer; degrade to full-text
        vectors = None
        embed_error = f"embedding failed, used full-text only: {type(e).__name__}"
    else:
        embed_error = None
    try:
        rows = get_store().kb_search(query, vectors[0] if vectors else None, top_k)
    except StoreError as e:
        return store_failure(e)
    passages = [{"title": r.get("title"), "source": r.get("source"), "text": r["content"]} for r in rows]
    return {"ok": True, "error": None, "passages": passages, "note": embed_error}
