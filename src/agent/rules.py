"""Conversation state the interaction tree decides on (owner: D).

record() runs after every tool call and keeps the facts the tree's checks
read: which codes were looked up (A2), tickets and IDs returned (A8, M3),
attempts per missing field (E6), how often the classifier was unsure (E3).
The decisions themselves are nodes in flow.yaml (src/agent/flow.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.config import policy


@dataclass
class RuleState:
    looked_up: set[str] = field(default_factory=set)        # A2
    ticket_ids: set[str] = field(default_factory=set)       # A8, M3
    known_ids: set[str] = field(default_factory=set)        # M3: every ID a tool returned
    field_attempts: dict[str, int] = field(default_factory=dict)  # E6
    unsure_count: int = 0                                   # E3
    handed_off: bool = False
    code: str | None = None
    confidence: float | None = None
    fields: dict = field(default_factory=dict)
    possible_duplicates: list[dict] = field(default_factory=list)


def unsure_reason(result: dict) -> str | None:
    """E3/F2: why a classifier result is too unsure to act on, or None if it is sure."""
    t = policy()["thresholds"]
    code, conf = result.get("code"), result.get("confidence") or 0.0
    if not result.get("ok", True) or code not in policy()["service_catalogue"]:
        return f"no valid code ({code or 'none'}), treated as confidence 0 (F2)"
    if conf < t["classifier_confidence"]:
        return f"{code} at {conf:.2f}, below {t['classifier_confidence']:.2f}"
    top2 = result.get("top2") or []
    if len(top2) > 1 and top2[0]["confidence"] - top2[1]["confidence"] < t["classifier_top2_margin"]:
        return (f"{top2[0]['code']} {top2[0]['confidence']:.2f} vs {top2[1]['code']} "
                f"{top2[1]['confidence']:.2f}, within {t['classifier_top2_margin']:.2f}")
    return None


def record(state: RuleState, name: str, args: dict, result: dict) -> None:
    if name == "classifier":
        if result.get("ok", True):
            state.code, state.confidence = result.get("code"), result.get("confidence")
        if unsure_reason(result):
            state.unsure_count += 1
    elif name == "policy_lookup" and result.get("ok"):
        state.looked_up.add(result["code"])
        state.code = result["code"]
    elif name == "search_tickets" and result.get("ok"):
        state.possible_duplicates = result.get("tickets", [])
        state.known_ids.update(t["ticket_id"] for t in state.possible_duplicates)
    elif name == "create_ticket":
        state.fields = {**state.fields, **(args.get("fields") or {})}   # kept even on failure (F3)
        if result.get("ok"):
            state.ticket_ids.add(result["ticket_id"])
            state.known_ids.add(result["ticket_id"])
        for f in result.get("missing") or []:
            state.field_attempts[f] = state.field_attempts.get(f, 0) + 1
    elif name == "get_status" and result.get("ok"):
        state.known_ids.add(result["ticket_id"])
    elif name == "handoff" and result.get("ok"):
        state.handed_off = True
        state.known_ids.add(result["ref_id"])
