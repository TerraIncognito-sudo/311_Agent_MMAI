"""Ticket tools (owner: D): search_tickets (A3), create_ticket (A4), get_status (A5).

Each returns a dict with "ok" and "error". The tools expose no edit, close,
cancel or reprioritize operation (A7 [hard]).
"""
from __future__ import annotations

import re

from src.guardrails.masking import redact
from src.tools.policy import policy_lookup
from src.tools.store import StoreError, get_store, store_failure

POSTAL_CODE = re.compile(r"\b([A-Za-z]\d[A-Za-z])\s?(\d[A-Za-z]\d)\b")


def _postal_code(*texts: str | None) -> str | None:
    for t in texts:
        m = POSTAL_CODE.search(t or "")
        if m:
            return f"{m.group(1)} {m.group(2)}".upper()
    return None


def _location(fields: dict) -> str | None:
    return fields.get("address") or fields.get("location")


def search_tickets(location: str, code: str | None = None, days: int | None = None) -> dict:
    """Open tickets near a location. D3 [hard]: id, code, status, date only."""
    if not location:
        return {"ok": False, "error": "location is required"}
    try:
        rows = get_store().find_open_tickets(location, code, days)
    except StoreError as e:
        return store_failure(e)
    tickets = [{"ticket_id": r["ticket_id"], "code": r["code"], "status": r["status"],
                "date_opened": str(r["opened_at"])[:10]} for r in rows]
    return {"ok": True, "error": None, "tickets": tickets}


def create_ticket(code: str, fields: dict, interaction_id: str | None = None) -> dict:
    """A4 [hard]: refuses unless the code exists and every required field is present.
    interaction_id is filled in by the orchestrator (not the model) so /admin can
    link the ticket to its conversation."""
    pol = policy_lookup(code)
    if not pol["ok"]:
        return pol
    fields = {k: redact(str(v))[0] for k, v in (fields or {}).items() if v not in (None, "")}  # D2
    missing = [f for f in pol["required_fields"] if not fields.get(f)]
    if missing:
        return {"ok": False, "error": f"missing required fields: {', '.join(missing)} (A4)", "missing": missing}
    location = _location(fields)
    contact = {k: fields.pop(k) for k in ("name", "phone", "email") if k in fields}
    row = {
        "code": pol["code"],
        "unit": pol["unit"],
        "location": location,
        "postal_code": fields.pop("postal_code", None) or _postal_code(location),
        "fields": fields,
        "contact": contact or None,
        "interaction_id": interaction_id,
    }
    try:
        saved = get_store().create_ticket(row)
    except StoreError as e:
        return store_failure(e)   # F3: caller must not claim a ticket exists
    return {"ok": True, "error": None, "ticket_id": saved["ticket_id"], "code": pol["code"],
            "category": pol["category"], "unit": pol["unit"], "location": location}


def get_status(ticket_id: str, postal_code: str) -> dict:
    """D5 [hard]: needs the ticket number AND the postal code on that ticket."""
    if not ticket_id or not postal_code:
        return {"ok": False, "error": "ticket number and postal code are both required (D5)"}
    try:
        t = get_store().get_ticket(ticket_id.strip().upper())
    except StoreError as e:
        return store_failure(e)
    given = _postal_code(postal_code)
    if not t or not t.get("postal_code") or given != t["postal_code"]:
        # Same answer for "no such ticket" and "wrong postal code" so IDs can't be probed.
        return {"ok": False, "error": "no ticket matches that number and postal code"}
    return {"ok": True, "error": None, "ticket_id": t["ticket_id"], "code": t["code"],
            "status": t["status"], "unit": t.get("unit"), "date_opened": str(t["opened_at"])[:10]}
