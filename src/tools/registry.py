"""Tool registry: the only actions the model can take (A1-A6, plus kb_search for C5).

TOOL_SCHEMAS is what the model sees (OpenAI function-calling format).
There is deliberately no edit/close/cancel/reprioritize tool (A7 [hard]).
handoff's package is assembled by the orchestrator, not the model, so the
model only supplies the rule ID and a short reason.
"""
from __future__ import annotations

from src.models.classifier import classify
from src.tools.handoff import handoff
from src.tools.knowledge import kb_search
from src.tools.policy import policy_lookup, valid_codes
from src.tools.tickets import create_ticket, get_status, search_tickets


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


def tool_schemas() -> list[dict]:
    codes = valid_codes()
    return [
        _fn("classifier", "Classify the resident's request into a service code. Returns code, confidence and top2.",
            {"text": {"type": "string", "description": "The resident's description of the issue"}}, ["text"]),
        _fn("policy_lookup", "Get a service code's unit, required fields and escalation triggers. Call before asking for fields or creating a ticket.",
            {"code": {"type": "string", "enum": codes}}, ["code"]),
        _fn("search_tickets", "Find open tickets near a location (to avoid duplicates).",
            {"location": {"type": "string"}, "code": {"type": "string", "enum": codes},
             "days": {"type": "integer", "description": "Only tickets opened in the last N days"}}, ["location"]),
        _fn("create_ticket", "Create ONE ticket once every required field is collected and no escalation trigger applies.",
            {"code": {"type": "string", "enum": codes},
             "fields": {"type": "object", "description": "Required fields by name (from policy_lookup); optional postal_code, name, phone, email"}},
            ["code", "fields"]),
        _fn("get_status", "Status of the resident's own ticket. Needs the ticket number and the postal code on it.",
            {"ticket_id": {"type": "string"}, "postal_code": {"type": "string"}}, ["ticket_id", "postal_code"]),
        _fn("handoff", "Pass the request to a 311 staff member (or log a 911 redirect). Use the policy rule ID that applies.",
            {"rule_id": {"type": "string", "enum": ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10"]},
             "reason": {"type": "string", "description": "One sentence on why"}}, ["rule_id", "reason"]),
        _fn("kb_search", "Search the City knowledge base for facts about services, schedules and rules. Only state facts this returns.",
            {"query": {"type": "string"}}, ["query"]),
    ]


TOOLS = {
    "classifier": lambda text: classify(text) | {"ok": True, "error": None},
    "policy_lookup": policy_lookup,
    "search_tickets": search_tickets,
    "create_ticket": create_ticket,
    "get_status": get_status,
    "handoff": handoff,
    "kb_search": kb_search,
}
