"""Input screen (owner: B): runs on every resident message before the model.

Returns flags; src/agent/orchestrator.py decides what to do with them:
  emergency     -> E1 [hard] 911 wording, no ticket, no model call
  self_harm     -> E8 handoff with crisis line
  injection     -> S1/S5 flagged and logged; message still treated as data
  human_request -> E7 handoff
  redacted      -> D2 [hard] items removed before anything else sees the text
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.config import config_cache, guardrails_config
from src.guardrails.masking import redact


@dataclass
class ScreenResult:
    text: str                      # redacted text, safe to pass on
    flags: list[str] = field(default_factory=list)
    redacted: list[str] = field(default_factory=list)

    def has(self, flag: str) -> bool:
        return flag in self.flags


@config_cache
def _patterns() -> dict[str, list[re.Pattern]]:
    cfg = guardrails_config()["input_screen"]
    return {name: [re.compile(p, re.IGNORECASE) for p in pats] for name, pats in cfg.items()}


def screen(text: str) -> ScreenResult:
    clean, redacted = redact(text)
    flags = [name for name, pats in _patterns().items() if any(p.search(clean) for p in pats)]
    return ScreenResult(text=clean, flags=flags, redacted=redacted)
