"""D2 redaction and D6 masking (owner: B).

redact(): removes data the agent must never store or pass on (D2) — applied
    to resident text before it reaches the model, the store or the logs.
mask(): replaces personal details with tags such as [PHONE] (D6) — applied
    to every log write. The model still sees addresses (it needs them).
"""
from __future__ import annotations

import re
from functools import lru_cache

from src.config import guardrails_config


@lru_cache
def _redaction_patterns() -> list[tuple[str, re.Pattern]]:
    cfg = guardrails_config()["redaction"]
    return [(name, re.compile(p, re.IGNORECASE)) for name, p in cfg.items()]


@lru_cache
def _masking_patterns() -> list[tuple[str, re.Pattern]]:
    cfg = guardrails_config()["masking"]
    return [(name, re.compile(p)) for name, p in cfg.items()]


def redact(text: str) -> tuple[str, list[str]]:
    """Return (text with D2 data removed, names of what was found)."""
    found = []
    for name, pattern in _redaction_patterns():
        if pattern.search(text):
            found.append(name)
            text = pattern.sub("[REDACTED]", text)
    return text, found


def mask(text: str | None) -> str | None:
    """D6: mask names, phones, emails, street numbers and unit numbers."""
    if not text:
        return text
    text, _ = redact(text)
    for name, pattern in _masking_patterns():
        tag = f"[{name.upper()}]"
        if pattern.groups:
            # Mask only the captured part (e.g. the name after "my name is").
            text = pattern.sub(lambda m: m.group(0).replace(m.group(1), tag), text)
        else:
            text = pattern.sub(tag, text)
    return text


def mask_obj(obj):
    """Mask every string inside a JSON-like structure."""
    if isinstance(obj, str):
        return mask(obj)
    if isinstance(obj, dict):
        return {k: mask_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_obj(v) for v in obj]
    return obj
