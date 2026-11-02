"""Orchestrator loop (owner: D): one agent, narrow tools, a decision tree, clean handoffs.

handle_turn() runs one resident message through the interaction tree
(flow.yaml, src/agent/flow.py) at fixed points ("hooks"):

  input screen (D2 redaction)      -> "message" tree (E1 911, E8, E7, S1 ...)
  model <-> tools loop             -> "before_<tool>" / "after_<tool>" trees
                                      (A2, A8, D5 blocks; E3, C6, M2 guidance; E6 handoff)
  tool or model failure            -> "tool_failure" / "model_failure" (E9)
  step limit                       -> "step_limit" (M4)
  the model's reply                -> "reply" tree (D8, M3, C1)

Every step, including every tree node visited and the branch it took, is
logged to interaction_events with masked text (D6).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

from src.agent import flow
from src.agent.rules import RuleState, record
from src.config import app_config, load_prompt, policy
from src.guardrails import output_check
from src.guardrails.input_screen import screen
from src.guardrails.masking import mask
from src.models.llm import LLMError, LLMSelection, chat
from src.tools.handoff import handoff
from src.tools.registry import TOOLS, tool_schemas
from src.tools.store import get_store


class SetupError(RuntimeError):
    """The chosen model can't be used (bad key, unknown model). Shown to the user."""


@dataclass
class Session:
    interaction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[dict] = field(default_factory=list)      # model context
    transcript: list[dict] = field(default_factory=list)    # resident-visible turns
    state: RuleState = field(default_factory=RuleState)
    last_seen: float = field(default_factory=time.time)


_SESSIONS: dict[str, Session] = {}


def get_session(interaction_id: str | None) -> Session:
    ttl = app_config()["agent"]["session_ttl_minutes"] * 60
    now = time.time()
    for sid in [s for s, v in _SESSIONS.items() if now - v.last_seen > ttl]:
        del _SESSIONS[sid]
    session = _SESSIONS.get(interaction_id or "")
    if session is None:
        session = Session()
        session.messages.append({"role": "system", "content": system_prompt()})
        _SESSIONS[session.interaction_id] = session
    session.last_seen = now
    return session


def system_prompt() -> str:
    return render_system_prompt(policy(), load_prompt(app_config()["agent"]["system_prompt"]))


def render_system_prompt(pol: dict, template: str) -> str:
    """Fill the prompt template from the policy. Also used by /admin to check an edit before saving."""
    catalogue = "\n".join(f"  - {code}: {e['category']} (routes to {e['unit']})"
                          for code, e in pol["service_catalogue"].items())
    return template.format(
        city=pol["city"], catalogue=catalogue, max_words=pol["thresholds"]["max_reply_words"],
        **{f"w_{k}": v for k, v in pol["wording"].items()},
    )


def log_event(session: Session, step: str, *, model: str | None = None, tokens_in: int = 0,
              tokens_out: int = 0, cost_usd: float | None = None, latency_ms: int | None = None,
              rule_id: str | None = None, outcome: str | None = None, text: str | None = None) -> None:
    """Event log contract (docs/interfaces.md). Text is always masked (D6 [hard])."""
    row = {"event_id": str(uuid.uuid4()), "interaction_id": session.interaction_id, "step": step,
           "model": model, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd,
           "latency_ms": latency_ms, "rule_id": rule_id, "outcome": outcome, "text": mask(text)}
    try:
        get_store().log_event(row)
    except Exception as e:  # logging must never break the conversation (F5 still prints)
        print(f"[event log failed] {step}: {type(e).__name__}")


def _run_flow(session: Session, hook: str, ctx: flow.Ctx) -> flow.Outcome:
    """Run one hook's tree, logging every node visited as a "flow" event."""
    def trace(step: dict):
        log_event(session, "flow", rule_id=step["rule"] if step["fired"] else None,
                  outcome=step["branch"], text=json.dumps(step))
    return flow.run(hook, ctx, trace)


def _wording(key: str, **kw) -> str:
    return policy()["wording"][key].format(**kw)


def _do_handoff(session: Session, rule_id: str, reason: str) -> str:
    st = session.state
    result = handoff(rule_id, {
        "interaction_id": session.interaction_id, "code": st.code, "confidence": st.confidence,
        "fields": st.fields, "transcript": session.transcript,
        "possible_duplicates": st.possible_duplicates, "reason": reason,
    })
    log_event(session, "tool:handoff", rule_id=rule_id, outcome="ok" if result["ok"] else "error",
              text=json.dumps({"reason": reason, "result": result}))
    st.handed_off = True
    if not result["ok"]:
        # F4/E9: even the handoff failed; never claim anything was recorded.
        return "Something went wrong on our side and I couldn't record this. Please call 311 directly."
    st.known_ids.add(result["ref_id"])
    return _wording("handoff", ref_id=result["ref_id"])


def _finish(session: Session, reply: str, rule_id: str | None = None) -> dict:
    session.transcript.append({"role": "assistant", "text": reply})
    session.messages.append({"role": "assistant", "content": reply})
    log_event(session, "reply", rule_id=rule_id, text=reply)   # what the resident saw
    return {"interaction_id": session.interaction_id, "reply": reply, "rule_id": rule_id}


def _end(session: Session, out: flow.Outcome) -> dict:
    """Finish the turn the way a tree's reply or handoff action decided."""
    if out.kind == "reply":
        return _finish(session, out.text, out.rule)
    prefix = _wording(out.prefix) + " " if out.prefix else ""
    return _finish(session, prefix + _do_handoff(session, out.handoff_rule or out.rule, out.reason), out.rule)


def _chat_with_retry(session: Session, sel: LLMSelection):
    retries = policy()["thresholds"]["tool_retries"]
    for attempt in range(retries + 1):
        try:
            resp = chat(sel, session.messages, tool_schemas())
        except LLMError as e:
            log_event(session, "model", model=sel.label, outcome="error", rule_id="F1", text=str(e))
            if not e.retryable:
                raise SetupError(str(e)) from e
            if attempt == retries:
                return None
            continue
        content = resp.message.get("content") or ""
        log_event(session, "model", model=sel.label, tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
                  cost_usd=resp.cost_usd, latency_ms=resp.latency_ms,
                  outcome="tool_calls" if resp.message.get("tool_calls") else "reply", text=content)
        return resp
    return None


def _run_tool(session: Session, name: str, args: dict) -> tuple[dict, flow.Outcome | None]:
    """Returns (result for the model, an outcome that ends the turn or None)."""
    st = session.state
    if name not in TOOLS:
        return {"ok": False, "error": f"no such tool {name!r} (A9)"}, None
    if name == "handoff":   # the model decided a person must review this (A6)
        rule = args.get("rule_id") or "M4"
        return {"ok": True, "error": None, "note": "handled"}, flow.Outcome(
            "handoff", rule, reason=args.get("reason") or rule, handoff_rule=rule)

    ctx = flow.Ctx(state=st, tool=name, args=args)
    before = _run_flow(session, f"before_{name}", ctx)
    if before.kind == "block":
        log_event(session, f"tool:{name}", rule_id=before.rule, outcome="blocked", text=before.text)
        return {"ok": False, "error": before.text}, None
    if before.kind in ("reply", "handoff"):
        return {"ok": False, "error": "skipped: request handed off"}, before

    retries = policy()["thresholds"]["tool_retries"]
    for attempt in range(retries + 1):
        start = time.perf_counter()
        try:
            call_args = {**args, "interaction_id": session.interaction_id} if name == "create_ticket" else args
            result = TOOLS[name](**call_args)
        except TypeError as e:   # model passed wrong arguments
            result = {"ok": False, "error": f"bad arguments: {e}"}
            break
        except Exception as e:   # F1: unexpected tool failure, retry once
            result = {"ok": False, "error": f"tool failure: {type(e).__name__}", "_failure": True}
        latency = int((time.perf_counter() - start) * 1000)
        failed = bool(result.get("_failure"))
        log_event(session, f"tool:{name}", latency_ms=latency, outcome="ok" if result["ok"] else "error",
                  rule_id="F5" if failed else None,
                  text=json.dumps({"args": args, "error": result.get("_detail") or result.get("error"),
                                   "result": {k: v for k, v in result.items() if not k.startswith("_")}}))
        if not failed:
            break
    record(st, name, args, result)   # collected fields are kept even on failure (F3)
    public = {k: v for k, v in result.items() if not k.startswith("_")}

    ctx.result = public
    if result.get("_failure"):
        return public, _run_flow(session, "tool_failure", ctx)
    after = _run_flow(session, f"after_{name}", ctx)
    if after.kind in ("reply", "handoff"):
        return public, after
    if ctx.guidance:
        public["guidance"] = " ".join(ctx.guidance)   # what the tree tells the model to do next
    return public, None


def handle_turn(interaction_id: str | None, user_text: str, sel: LLMSelection) -> dict:
    session = get_session(interaction_id)
    scr = screen(user_text)
    session.state.mentioned_ids |= output_check.ids_in(scr.text)   # M3: may be quoted back
    session.transcript.append({"role": "resident", "text": scr.text})
    log_event(session, "input_screen", outcome=",".join(scr.flags + scr.redacted) or "clean", text=scr.text)

    ctx = flow.Ctx(state=session.state, screen=scr)
    out = _run_flow(session, "message", ctx)
    if out.kind in ("reply", "handoff"):
        return _end(session, out)
    content = scr.text
    if ctx.guidance:
        content += "\n\n[" + " ".join(ctx.guidance) + "]"
    session.messages.append({"role": "user", "content": content})

    for _ in range(app_config()["llm"]["max_tool_steps"]):
        resp = _chat_with_retry(session, sel)
        if resp is None:                                      # F1 -> E9
            return _end(session, _run_flow(session, "model_failure", flow.Ctx(state=session.state)))
        msg = resp.message
        calls = msg.get("tool_calls") or []
        if not calls:
            rctx = flow.Ctx(state=session.state, reply=(msg.get("content") or "").strip())
            out = _run_flow(session, "reply", rctx)
            if out.kind in ("reply", "handoff"):
                return _end(session, out)
            return _finish(session, rctx.reply)

        session.messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        ending = None
        for call in calls:
            name = call["function"]["name"]
            if ending:   # the turn already ended; answer remaining calls so the context stays valid
                result = {"ok": False, "error": "skipped: request handed off"}
            else:
                try:
                    raw = call["function"].get("arguments") or "{}"
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    args = {}
                result, ending = _run_tool(session, name, args)
            session.messages.append({"role": "tool", "tool_call_id": call.get("id", name), "name": name,
                                     "content": json.dumps(result)})
        if ending and ending.kind in ("reply", "handoff"):
            return _end(session, ending)

    return _end(session, _run_flow(session, "step_limit", flow.Ctx(state=session.state)))
