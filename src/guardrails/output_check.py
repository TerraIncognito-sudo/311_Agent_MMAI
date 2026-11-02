"""Output check (owner: B): runs on every agent reply before the resident sees it.

M3 [hard]: a ticket ID or handoff reference that no tool returned in this
    conversation means the model invented it; the reply is blocked.
C1/C2/C4: commitment phrases (dates, priorities, refunds) are flagged and
    logged. Extend here to rewrite or block them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from src.config import guardrails_config


@dataclass
class OutputResult:
    ok: bool
    flags: list[str] = field(default_factory=list)
    rule_id: str | None = None


@lru_cache
def _patterns():
    cfg = guardrails_config()["output_check"]
    return (
        [re.compile(p, re.IGNORECASE) for p in cfg["commitment"]],
        re.compile(cfg["ticket_id"]),
        re.compile(cfg["handoff_ref"]),
        re.compile(cfg["ticket_claim"], re.IGNORECASE),
    )


def check(reply: str, known_ids: set[str], ticket_created: bool = True) -> OutputResult:
    commitment, ticket_re, handoff_re, claim_re = _patterns()
    ids = set(ticket_re.findall(reply)) | set(handoff_re.findall(reply))
    invented = ids - known_ids
    if invented:
        return OutputResult(ok=False, flags=[f"invented_id:{i}" for i in sorted(invented)], rule_id="M3")
    if not ticket_created and claim_re.search(reply):
        return OutputResult(ok=False, flags=["claimed_ticket_without_create"], rule_id="M3")
    flags = ["commitment" for p in commitment if p.search(reply)][:1]
    return OutputResult(ok=True, flags=flags, rule_id="C1" if flags else None)
