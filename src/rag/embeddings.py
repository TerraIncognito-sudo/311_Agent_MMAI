"""Embeddings for the knowledge base (optional; see app.yaml retrieval in the Supabase config).

With embedding_provider "none" this returns None and retrieval falls back to
Postgres full-text search. The model and dimensions must match the
vector(...) column in supabase/migrations.
"""
from __future__ import annotations

import httpx

from src.config import app_config, env


def embed(texts: list[str]) -> list[list[float]] | None:
    cfg = app_config()["retrieval"]
    if cfg["embedding_provider"] == "none" or not texts:
        return None
    if cfg["embedding_provider"] != "ollama":
        raise ValueError(f"unknown embedding_provider {cfg['embedding_provider']!r}")
    r = httpx.post(f"{env('OLLAMA_BASE_URL', 'http://localhost:11434')}/api/embed",
                   json={"model": cfg["embedding_model"], "input": texts}, timeout=60)
    r.raise_for_status()
    vectors = r.json()["embeddings"]
    if vectors and len(vectors[0]) != cfg["embedding_dimensions"]:
        raise ValueError(f"embedding size {len(vectors[0])} != embedding_dimensions {cfg['embedding_dimensions']}")
    return vectors
