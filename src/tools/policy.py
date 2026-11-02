"""policy_lookup(code) — A2 (owner: D). Reads config/policy.yaml only."""
from __future__ import annotations

from src.config import policy


def policy_lookup(code: str) -> dict:
    entry = policy()["service_catalogue"].get((code or "").strip().upper())
    if entry is None:
        return {"ok": False, "error": f"unknown code {code!r} (A1)"}
    return {
        "ok": True,
        "error": None,
        "code": code.strip().upper(),
        "category": entry["category"],
        "unit": entry["unit"],
        "required_fields": list(entry["required_fields"]),
        "field_options": entry.get("field_options", {}),
        "escalation_triggers": list(entry["escalation_triggers"]),
        "duplicate_window": entry["duplicate_window"],
    }


def valid_codes() -> list[str]:
    return list(policy()["service_catalogue"])
