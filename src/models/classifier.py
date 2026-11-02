"""classify(text) -> {code, confidence, top2}  (owner: C, W3 Models)

Shared interface for the prompted and fine-tuned classifiers. This is the
STUB the plan calls for until Nov 16: a keyword scorer over the policy
catalogue so the agent runs end to end. Replace the body of classify() with
the real model; keep the signature and return shape unchanged.
"""
from __future__ import annotations

import re

from src.tools.policy import valid_codes

# Stub vocabulary only; the real classifier's prompts go in prompts/classifier/ (Supabase config, /admin).
_KEYWORDS = {
    "SW-BIN": ["bin", "cart", "lid", "container", "broken bin", "stolen bin"],
    "SW-MISS": ["missed", "not picked up", "wasn't collected", "collection", "pickup", "garbage day"],
    "AN-DEAD": ["dead", "carcass", "roadkill"],
    "AN-INJ": ["injured", "hurt animal", "limping", "distressed", "bleeding animal", "trapped"],
    "RD-POT": ["pothole", "road damage", "crack in the road", "sinkhole", "road"],
    "TR-TREE": ["tree", "branch", "limb", "pruning"],
    "PS-PROP": ["property", "landlord", "mould", "mold", "no heat", "overgrown", "building"],
    "IL-DUMP": ["dumped", "dumping", "mattress", "couch", "junk", "needles", "debris"],
}


def classify(text: str) -> dict:
    text = (text or "").lower()
    scores = {code: sum(len(re.findall(r"\b" + re.escape(k) + r"\b", text)) for k in kws)
              for code, kws in _KEYWORDS.items() if code in valid_codes()}
    total = sum(scores.values())
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if total == 0:
        return {"code": None, "confidence": 0.0, "top2": []}
    top2 = [{"code": c, "confidence": round(s / total, 2)} for c, s in ranked[:2] if s]
    return {"code": top2[0]["code"], "confidence": top2[0]["confidence"], "top2": top2}
