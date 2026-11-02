"""Deterministic rules layer (owner: D). Can overrule the model.

before_tool() runs before every tool call and may block it with an error the
model sees. after_tool() records what tools returned and may force a handoff.
Each check cites the [hard] rule it enforces.
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
    handed_off: bool = False
    code: str | None = None
    confidence: float | None = None
    fields: dict = field(default_factory=dict)
    possible_duplicates: list[dict] = field(default_factory=list)


@dataclass
class Decision:
    allow: bool = True
    error: str | None = None
    rule_id: str | None = None
    force_handoff: str | None = None   # rule ID to hand off under


def before_tool(state: RuleState, name: str, args: dict) -> Decision:
    if name == "create_ticket":
        code = (args.get("code") or "").upper()
        if state.handed_off:
            return Decision(False, "this request was already handed off; do not create a ticket", "A4")
        if state.ticket_ids:
            return Decision(False, "a ticket was already created for this request (A8)", "A8")
        if code not in state.looked_up:
            return Decision(False, f"call policy_lookup for {code} before creating a ticket (A2)", "A2")
    if name == "get_status" and not (args.get("ticket_id") and args.get("postal_code")):
        return Decision(False, "ask for both the ticket number and the postal code on it (D5)", "D5")
    return Decision()


def after_tool(state: RuleState, name: str, args: dict, result: dict) -> Decision:
    if name == "classifier" and result.get("ok"):
        state.code, state.confidence = result.get("code"), result.get("confidence")
    elif name == "policy_lookup" and result.get("ok"):
        state.looked_up.add(result["code"])
        state.code = result["code"]
    elif name == "search_tickets" and result.get("ok"):
        state.possible_duplicates = result.get("tickets", [])
        state.known_ids.update(t["ticket_id"] for t in state.possible_duplicates)
    elif name == "create_ticket":
        state.fields = {**state.fields, **(args.get("fields") or {})}
        if result.get("ok"):
            state.ticket_ids.add(result["ticket_id"])
            state.known_ids.add(result["ticket_id"])
        elif result.get("missing"):
            # E6 [hard]: count failed attempts per field; hand off past the limit.
            limit = policy()["thresholds"]["max_field_attempts"]
            for f in result["missing"]:
                state.field_attempts[f] = state.field_attempts.get(f, 0) + 1
                if state.field_attempts[f] > limit:
                    return Decision(force_handoff="E6", rule_id="E6")
    elif name == "get_status" and result.get("ok"):
        state.known_ids.add(result["ticket_id"])
    elif name == "handoff" and result.get("ok"):
        state.handed_off = True
        state.known_ids.add(result["ref_id"])
    return Decision()
