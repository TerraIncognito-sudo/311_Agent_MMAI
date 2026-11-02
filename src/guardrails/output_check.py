"""Output check (owner: B): the checks run on every agent reply before the
resident sees it. The interaction tree (flow.yaml, "reply" hook) decides what
happens when one fires.

M3 [hard]: a ticket ID or handoff reference that no tool returned in this
    conversation means the model invented it; the reply is blocked.
C1/C2/C4: commitment phrases (dates, priorities, refunds) are flagged and
    logged. Extend here to rewrite or block them.
D8: policy rule IDs the model echoes from its prompt, such as "(M1)", are
    removed by strip_rule_ids() before the reply is checked or shown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.config import config_cache, guardrails_config


@dataclass
class OutputResult:
    ok: bool
    flags: list[str] = field(default_factory=list)
    rule_id: str | None = None


@config_cache
def _patterns():
    cfg = guardrails_config()["output_check"]
    return (
        [re.compile(p, re.IGNORECASE) for p in cfg["commitment"]],
        re.compile(cfg["ticket_id"]),
        re.compile(cfg["handoff_ref"]),
        re.compile(cfg["ticket_claim"], re.IGNORECASE),
    )


@config_cache
def _rule_tag():
    return re.compile(guardrails_config()["output_check"]["rule_id_tag"], re.IGNORECASE)


def strip_rule_ids(reply: str) -> tuple[str, bool]:
    """D8: remove parenthesised rule IDs. Returns (clean reply, whether any were removed)."""
    clean = _rule_tag().sub("", reply)
    if clean == reply:
        return reply, False
    clean = re.sub(r"[ \t]+([.,;:!?])", r"\1", clean)   # "assistant (M1)." -> "assistant."
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    return clean.strip(), True


def ids_in(text: str) -> set[str]:
    """Ticket and handoff reference IDs in a text."""
    _, ticket_re, handoff_re, _ = _patterns()
    return set(ticket_re.findall(text or "")) | set(handoff_re.findall(text or ""))


def invented_ids(reply: str, known_ids: set[str]) -> list[str]:
    """M3 [hard]: ticket or handoff IDs in the reply that no tool returned (and the
    resident didn't give: repeating their own ticket number is not inventing one)."""
    return sorted(ids_in(reply) - known_ids)


def claim_phrase(reply: str) -> str | None:
    """M3 [hard]: text claiming a request was created/filed (checked when none was)."""
    m = _patterns()[3].search(reply)
    return m.group(0) if m else None


def commitment_phrase(reply: str) -> str | None:
    """C1/C2/C4: the first commitment phrase in the reply."""
    for p in _patterns()[0]:
        m = p.search(reply)
        if m:
            return m.group(0)
    return None


def check(reply: str, known_ids: set[str], ticket_created: bool = True) -> OutputResult:
    """All three checks at once. The agent runs them one by one through the
    interaction tree (flow.yaml, "reply" hook); this is kept for tests and tools."""
    invented = invented_ids(reply, known_ids)
    if invented:
        return OutputResult(ok=False, flags=[f"invented_id:{i}" for i in invented], rule_id="M3")
    if not ticket_created and claim_phrase(reply):
        return OutputResult(ok=False, flags=["claimed_ticket_without_create"], rule_id="M3")
    flags = ["commitment"] if commitment_phrase(reply) else []
    return OutputResult(ok=True, flags=flags, rule_id="C1" if flags else None)
