"""Data store behind the tools (owner: D).

SupabaseStore talks to Supabase's REST API (PostgREST) with the server-side
service key. MemoryStore is a drop-in for tests and for running without
Supabase (USE_MEMORY_STORE=1); it starts with the config test fixture and
nothing persists across restarts.

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

from src.config import env, fixture_files


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

    def list_documents(self) -> list[dict]:
        return self._req("GET", "kb_documents", params={
            "select": "id,source,title,content,tags,active,updated_at,chunked_at", "order": "title"})

    def update_document(self, document_id: str, fields: dict) -> dict | None:
        rows = self._req("PATCH", "kb_documents", params={"id": f"eq.{document_id}"}, json=fields,
                         headers={"Prefer": "return=representation"})
        return rows[0] if rows else None

    def delete_document(self, document_id: str) -> None:
        self._req("DELETE", "kb_documents", params={"id": f"eq.{document_id}"})

    # --- configuration (src/config.py); edited in /admin ---
    def list_config(self) -> list[dict]:
        return self._req("GET", "config_files", params={"select": "path,content,updated_at,updated_by"})

    def save_config(self, path: str, content: str, author: str | None, note: str | None) -> None:
        self._req("POST", "config_files", params={"on_conflict": "path"},
                  json={"path": path, "content": content, "updated_at": _now().isoformat(), "updated_by": author},
                  headers={"Prefer": "return=minimal,resolution=merge-duplicates"})
        self._insert("config_revisions", {"path": path, "content": content, "author": author, "note": note})

    def delete_config(self, path: str, author: str | None) -> None:
        self._req("DELETE", "config_files", params={"path": f"eq.{path}"})
        self._insert("config_revisions", {"path": path, "content": None, "author": author, "note": "deleted"})

    def config_revisions(self, path: str, limit: int = 50) -> list[dict]:
        return self._req("GET", "config_revisions", params={
            "select": "id,path,content,note,author,saved_at", "path": f"eq.{path}",
            "order": "saved_at.desc", "limit": str(limit)})

    # --- interaction walkthrough for /admin ---
    def list_interactions(self, limit: int = 50) -> list[dict]:
        return self._req("GET", "interaction_summaries", params={
            "select": "*", "order": "last_at.desc", "limit": str(limit)})

    def interaction_events(self, interaction_id: str) -> list[dict]:
        return self._req("GET", "interaction_events", params={
            "select": "*", "interaction_id": f"eq.{interaction_id}", "order": "created_at.asc"})

    def handoffs_for(self, interaction_id: str) -> list[dict]:
        return self._req("GET", "handoffs", params={
            "select": "*", "interaction_id": f"eq.{interaction_id}", "order": "created_at.asc"})

    def tickets_by_id(self, ticket_ids: list[str]) -> list[dict]:
        if not ticket_ids:
            return []
        return self._req("GET", "tickets", params={
            "select": "ticket_id,code,unit,status,location,postal_code,fields,opened_at",
            "ticket_id": f"in.({','.join(ticket_ids)})"})


class MemoryStore:
    """In-memory store with the same methods as SupabaseStore."""

    def __init__(self, config: dict[str, str] | None = None):
        self.tickets: list[dict] = []
        self.handoffs: list[dict] = []
        self.events: list[dict] = []
        self.documents: list[dict] = []
        self.chunks: list[dict] = []
        self.configs: dict[str, dict] = {p: {"content": c, "updated_at": _now().isoformat(), "updated_by": "fixture"}
                                          for p, c in (config or {}).items()}
        self.revisions: list[dict] = []
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
        self.events.append({"created_at": _now().isoformat(), **row})

    def kb_search(self, query: str, embedding, top_k: int) -> list[dict]:
        self._check("kb_search")
        words = set(re.findall(r"\w{3,}", query.lower()))
        active = {d["id"] for d in self.documents if d.get("active", True)}
        scored = []
        for c in self.chunks:
            if c["document_id"] not in active:
                continue
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

    def list_documents(self) -> list[dict]:
        return sorted(self.documents, key=lambda d: d["title"])

    def update_document(self, document_id: str, fields: dict) -> dict | None:
        doc = next((d for d in self.documents if d["id"] == document_id), None)
        if doc is None:
            return None
        if any(fields.get(k, doc.get(k)) != doc.get(k) for k in ("content", "title")):
            doc["chunked_at"] = None
        doc.update(fields)
        return doc

    def delete_document(self, document_id: str) -> None:
        self.documents = [d for d in self.documents if d["id"] != document_id]
        self.chunks = [c for c in self.chunks if c["document_id"] != document_id]

    def list_config(self) -> list[dict]:
        self._check("list_config")
        return [{"path": p, **v} for p, v in self.configs.items()]

    def save_config(self, path: str, content: str, author: str | None, note: str | None) -> None:
        self.configs[path] = {"content": content, "updated_at": _now().isoformat(), "updated_by": author}
        self.revisions.append({"id": len(self.revisions) + 1, "path": path, "content": content,
                               "note": note, "author": author, "saved_at": _now().isoformat()})

    def delete_config(self, path: str, author: str | None) -> None:
        self.configs.pop(path, None)
        self.revisions.append({"id": len(self.revisions) + 1, "path": path, "content": None,
                               "note": "deleted", "author": author, "saved_at": _now().isoformat()})

    def config_revisions(self, path: str, limit: int = 50) -> list[dict]:
        return [r for r in reversed(self.revisions) if r["path"] == path][:limit]

    def list_interactions(self, limit: int = 50) -> list[dict]:
        by_id: dict[str, list[dict]] = {}
        for e in self.events:
            by_id.setdefault(e["interaction_id"], []).append(e)
        out = []
        for iid, evs in by_id.items():
            screens = [e for e in evs if e["step"] == "input_screen"]
            out.append({
                "interaction_id": iid, "started_at": evs[0].get("created_at"), "last_at": evs[-1].get("created_at"),
                "events": len(evs), "turns": len(screens),
                "models": ", ".join(sorted({e["model"] for e in evs if e.get("model")})) or None,
                "rule_ids": sorted({e["rule_id"] for e in evs if e.get("rule_id")}) or None,
                "tickets": sum(e["step"] == "tool:create_ticket" and e["outcome"] == "ok" for e in evs),
                "handoffs": sum(e["step"] == "tool:handoff" and e["outcome"] == "ok" for e in evs),
                "tokens_in": sum(e.get("tokens_in") or 0 for e in evs),
                "tokens_out": sum(e.get("tokens_out") or 0 for e in evs),
                "cost_usd": sum(e.get("cost_usd") or 0 for e in evs),
                "first_message": screens[0]["text"] if screens else None,
            })
        return list(reversed(out))[:limit]

    def interaction_events(self, interaction_id: str) -> list[dict]:
        return [e for e in self.events if e["interaction_id"] == interaction_id]

    def handoffs_for(self, interaction_id: str) -> list[dict]:
        return [h for h in self.handoffs if h.get("interaction_id") == interaction_id]

    def tickets_by_id(self, ticket_ids: list[str]) -> list[dict]:
        return [t for t in self.tickets if t["ticket_id"] in ticket_ids]


@lru_cache
def get_store():
    url, key = env("SUPABASE_URL"), env("SUPABASE_SERVICE_KEY")
    if env("USE_MEMORY_STORE", "0") == "1" or not (url and key):
        return MemoryStore(config=fixture_files())
    return SupabaseStore(url, key)
