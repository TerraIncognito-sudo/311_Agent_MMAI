"""Data store behind the tools (owner: D).

SupabaseStore talks to Supabase's REST API (PostgREST) with the server-side
service key. MemoryStore is a drop-in for tests and for running without
Supabase (USE_MEMORY_STORE=1); nothing persists across restarts.

Tables are defined in supabase/migrations/. Tools call the store; nothing
else in the agent touches the database directly.
"""
from __future__ import annotations

import itertools
import re
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import httpx

from src.config import env


class StoreError(RuntimeError):
    pass


def store_failure(e: StoreError) -> dict:
    """Tool result for an infrastructure failure. Keys starting with "_" are
    stripped before the model sees the result; the orchestrator retries once
    and then hands off (F1, E9)."""
    return {"ok": False, "error": "service temporarily unavailable", "_failure": True, "_detail": str(e)}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


class SupabaseStore:
    def __init__(self, url: str, key: str, timeout: float = 10.0):
        self._rest = url.rstrip("/") + "/rest/v1"
        self._client = httpx.Client(
            timeout=timeout,
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )

    def _req(self, method: str, path: str, **kw):
        try:
            r = self._client.request(method, f"{self._rest}/{path}", **kw)
        except httpx.HTTPError as e:
            raise StoreError(f"supabase unreachable: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise StoreError(f"supabase {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else None

    def _insert(self, table: str, row: dict) -> dict:
        rows = self._req("POST", table, json=row, headers={"Prefer": "return=representation"})
        return rows[0]

    # --- tickets ---
    def create_ticket(self, row: dict) -> dict:
        return self._insert("tickets", row)

    def find_open_tickets(self, location: str, code: str | None, days: int | None) -> list[dict]:
        # D3 [hard]: select only non-personal columns.
        params = {
            "select": "ticket_id,code,status,opened_at",
            "status": "neq.closed",
            "location_norm": f"ilike.*{_norm(location)}*",
            "order": "opened_at.desc",
            "limit": "10",
        }
        if code:
            params["code"] = f"eq.{code}"
        if days:
            params["opened_at"] = f"gte.{(_now() - timedelta(days=days)).isoformat()}"
        return self._req("GET", "tickets", params=params)

    def get_ticket(self, ticket_id: str) -> dict | None:
        rows = self._req("GET", "tickets", params={
            "select": "ticket_id,code,status,opened_at,postal_code,unit",
            "ticket_id": f"eq.{ticket_id}",
        })
        return rows[0] if rows else None

    # --- handoffs ---
    def create_handoff(self, row: dict) -> dict:
        return self._insert("handoffs", row)

    # --- event log ---
    def log_event(self, row: dict) -> None:
        self._req("POST", "interaction_events", json=row, headers={"Prefer": "return=minimal"})

    # --- knowledge base ---
    def kb_search(self, query: str, embedding: list[float] | None, top_k: int) -> list[dict]:
        body = {"query_text": query, "query_embedding": embedding, "match_count": top_k}
        return self._req("POST", "rpc/match_kb_chunks", json=body)

    def upsert_document(self, row: dict) -> dict:
        rows = self._req("POST", "kb_documents", params={"on_conflict": "source"}, json=row,
                         headers={"Prefer": "return=representation,resolution=merge-duplicates"})
        return rows[0]

    def documents_needing_chunks(self) -> list[dict]:
        return self._req("GET", "kb_documents_stale", params={"select": "*"})

    def replace_chunks(self, document_id: str, chunks: list[dict]) -> None:
        self._req("DELETE", "kb_chunks", params={"document_id": f"eq.{document_id}"})
        if chunks:
            self._req("POST", "kb_chunks", json=chunks, headers={"Prefer": "return=minimal"})
        self._req("PATCH", "kb_documents", params={"id": f"eq.{document_id}"},
                  json={"chunked_at": _now().isoformat()}, headers={"Prefer": "return=minimal"})


class MemoryStore:
    """In-memory store with the same methods as SupabaseStore."""

    def __init__(self):
        self.tickets: list[dict] = []
        self.handoffs: list[dict] = []
        self.events: list[dict] = []
        self.documents: list[dict] = []
        self.chunks: list[dict] = []
        self._ticket_seq = itertools.count(1)
        self._handoff_seq = itertools.count(1)
        self.fail: set[str] = set()   # method names that should raise (failure tests)

    def _check(self, name: str):
        if name in self.fail:
            raise StoreError(f"{name} failed (simulated)")

    def create_ticket(self, row: dict) -> dict:
        self._check("create_ticket")
        year = _now().year
        row = {**row, "ticket_id": f"311-{year}-{next(self._ticket_seq):06d}", "status": "open",
               "opened_at": _now().isoformat(), "location_norm": _norm(row.get("location"))}
        self.tickets.append(row)
        return row

    def find_open_tickets(self, location: str, code: str | None, days: int | None) -> list[dict]:
        self._check("find_open_tickets")
        cutoff = _now() - timedelta(days=days) if days else None
        out = []
        for t in self.tickets:
            if t["status"] == "closed" or _norm(location) not in t["location_norm"]:
                continue
            if code and t["code"] != code:
                continue
            if cutoff and datetime.fromisoformat(t["opened_at"]) < cutoff:
                continue
            out.append({k: t[k] for k in ("ticket_id", "code", "status", "opened_at")})
        return out

    def get_ticket(self, ticket_id: str) -> dict | None:
        self._check("get_ticket")
        return next((t for t in self.tickets if t["ticket_id"] == ticket_id), None)

    def create_handoff(self, row: dict) -> dict:
        self._check("create_handoff")
        row = {**row, "ref_id": f"HO-{_now().year}-{next(self._handoff_seq):06d}", "created_at": _now().isoformat()}
        self.handoffs.append(row)
        return row

    def log_event(self, row: dict) -> None:
        self.events.append(row)

    def kb_search(self, query: str, embedding, top_k: int) -> list[dict]:
        self._check("kb_search")
        words = set(re.findall(r"\w{3,}", query.lower()))
        scored = []
        for c in self.chunks:
            score = len(words & set(re.findall(r"\w{3,}", c["content"].lower())))
            if score:
                scored.append({**c, "score": score})
        return sorted(scored, key=lambda c: -c["score"])[:top_k]

    def upsert_document(self, row: dict) -> dict:
        existing = next((d for d in self.documents if d["source"] == row["source"]), None)
        if existing:
            if existing["content"] != row["content"]:
                existing["chunked_at"] = None
            existing.update(row)
            return existing
        row = {**row, "id": str(uuid.uuid4()), "chunked_at": None}
        self.documents.append(row)
        return row

    def documents_needing_chunks(self) -> list[dict]:
        return [d for d in self.documents if not d.get("chunked_at")]

    def replace_chunks(self, document_id: str, chunks: list[dict]) -> None:
        doc = next(d for d in self.documents if d["id"] == document_id)
        self.chunks = [c for c in self.chunks if c["document_id"] != document_id]
        self.chunks += [{**c, "title": doc["title"], "source": doc["source"]} for c in chunks]
        doc["chunked_at"] = _now().isoformat()


@lru_cache
def get_store():
    url, key = env("SUPABASE_URL"), env("SUPABASE_SERVICE_KEY")
    if env("USE_MEMORY_STORE", "0") == "1" or not (url and key):
        return MemoryStore()
    return SupabaseStore(url, key)
