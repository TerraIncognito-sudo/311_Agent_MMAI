"""Interaction tree engine (owner: D; content owned by B in flow.yaml).

flow.yaml (in Supabase, edited in /admin) holds the operating policy's
decision logic as trees of nodes. The orchestrator calls run(hook, ctx) at
fixed points of a turn (HOOKS). Each node is either

  a decision:  check + params -> "yes" / "no" branch (missing branch = carry on)
  an action:   action + params [-> next]

and every node visited is reported to the `trace` callback with the value it
checked and the branch it took, so a conversation can be replayed step by
step. The checks and actions are the vocabulary the code provides (CHECKS,
ACTIONS); the tree decides how they are wired.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from src.agent.rules import unsure_reason
from src.config import config_cache, flow_config, guardrails_config, policy
from src.guardrails import output_check

MAX_NODES_PER_RUN = 60   # cycle guard; validate() also rejects cycles

TOOL_HOOKS = ["classifier", "policy_lookup", "search_tickets", "create_ticket", "get_status", "kb_search"]
HOOKS = {
    "message": "A resident message arrives (after D2 redaction, before the model)",
    **{f"before_{t}": f"Before the agent runs {t}" for t in TOOL_HOOKS},
    **{f"after_{t}": f"After {t} returns" for t in TOOL_HOOKS},
    "tool_failure": "A tool still failed after one retry (F1)",
    "model_failure": "The model still failed after one retry (F1)",
    "step_limit": "No answer within llm.max_tool_steps (M4)",
    "reply": "The model wrote a reply, before the resident sees it",
}

# Hard rules the default tree enforces: a save that leaves one without an
# enabled node in its hook's tree is refused (src/web/admin.py).
HARD_RULES = {"E1": "message", "A2": "before_create_ticket", "A8": "before_create_ticket",
              "D5": "before_get_status", "E6": "after_create_ticket", "E9": "tool_failure", "M3": "reply"}


@dataclass
class Ctx:
    """What the checks can see at a hook."""
    state: Any                        # src.agent.rules.RuleState
    screen: Any = None                # src.guardrails.input_screen.ScreenResult
    tool: str | None = None
    args: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    reply: str | None = None
    guidance: list[str] = field(default_factory=list)
    observed: str = ""                # what the last check saw, for {observed} in guidance

    def vars(self) -> dict:
        """Placeholders for guidance, block messages and handoff reasons."""
        res, st = self.result or {}, self.state
        v = {**policy()["thresholds"], **{k: x for k, x in (self.args or {}).items() if isinstance(x, (str, int, float))},
             **{k: x for k, x in res.items() if isinstance(x, (str, int, float))},
             "tool": self.tool or "", "observed": self.observed,
             "missing": ", ".join(res.get("missing") or []),
             "ticket_ids": ", ".join(t["ticket_id"] for t in res.get("tickets") or []),
             "code": (self.args or {}).get("code") or res.get("code") or st.code or ""}
        return v


def _fmt(text: str, ctx: Ctx) -> str:
    return str(text).format_map(defaultdict(str, ctx.vars()))


@dataclass
class Outcome:
    kind: str = "proceed"             # proceed | reply | handoff | block
    rule: str | None = None           # rule ID the deciding node cites
    text: str | None = None           # reply text or block message
    reason: str | None = None         # handoff reason
    handoff_rule: str | None = None   # rule ID recorded on the handoff
    prefix: str | None = None         # approved wording shown before the handoff wording


# ------------------------------------------------------------------ checks
# name -> (label, params it takes, hooks it makes sense in, fn(ctx, **params) -> (bool, observed))

def _screen_flag(ctx, flag):
    hit = ctx.screen.matches.get(flag) if ctx.screen else None
    return bool(hit), f"matched /{hit}/" if hit else f"no {flag} pattern matched"


def _redacted(ctx):
    found = ctx.screen.redacted if ctx.screen else []
    return bool(found), f"redacted: {', '.join(found)}" if found else "nothing to redact"


def _classifier_unsure(ctx):
    why = unsure_reason(ctx.result)
    if why:
        return True, why
    return False, f"{ctx.result.get('code')} at {ctx.result.get('confidence', 0):.2f}"


def _unsure_count_over(ctx, times=1):
    n = ctx.state.unsure_count
    return n > int(times), f"unsure {n} time(s) in this conversation (limit {times})"


def _handed_off(ctx):
    return ctx.state.handed_off, "already handed off" if ctx.state.handed_off else "not handed off"


def _ticket_already_created(ctx):
    ids = sorted(ctx.state.ticket_ids)
    return bool(ids), f"already created: {', '.join(ids)}" if ids else "no ticket yet"


def _code_not_looked_up(ctx):
    code = (ctx.args.get("code") or "").upper()
    done = ", ".join(sorted(ctx.state.looked_up)) or "none"
    return code not in ctx.state.looked_up, f"creating {code or '?'}; looked up: {done}"


def _duplicates_found(ctx):
    ts = ctx.result.get("tickets") or []
    return bool(ts), f"{len(ts)} open ticket(s): {', '.join(t['ticket_id'] for t in ts)}" if ts else "no open tickets nearby"


def _ticket_created(ctx):
    tid = ctx.result.get("ticket_id") if ctx.result.get("ok") else None
    return bool(tid), f"created {tid}" if tid else f"not created: {ctx.result.get('error')}"


def _fields_missing(ctx):
    missing = ctx.result.get("missing") or []
    return bool(missing), f"missing: {', '.join(missing)}" if missing else "no missing fields reported"


def _field_attempts_over(ctx):
    limit = policy()["thresholds"]["max_field_attempts"]
    counts = {f: n for f, n in ctx.state.field_attempts.items() if f in (ctx.result.get("missing") or [])}
    over = [f for f, n in counts.items() if n > limit]
    seen = ", ".join(f"{f} x{n}" for f, n in counts.items()) or "none"
    return bool(over), f"attempts: {seen} (limit {limit})"


def _status_args_missing(ctx):
    missing = [k for k in ("ticket_id", "postal_code") if not ctx.args.get(k)]
    return bool(missing), f"missing: {', '.join(missing)}" if missing else "ticket number and postal code given"


def _reply_has_rule_ids(ctx):
    clean, found = output_check.strip_rule_ids(ctx.reply or "")
    return found, "reply contains rule IDs" if found else "no rule IDs"


def _reply_invented_id(ctx):
    ids = output_check.invented_ids(ctx.reply or "", ctx.state.known_ids | ctx.state.mentioned_ids)
    if ids:
        return True, f"not returned by any tool or given by the resident: {', '.join(ids)}"
    return False, "every ID came from a tool or the resident"


def _reply_claims_ticket(ctx):
    if ctx.state.ticket_ids:
        return False, f"ticket {', '.join(sorted(ctx.state.ticket_ids))} was created"
    phrase = output_check.claim_phrase(ctx.reply or "")
    return bool(phrase), f'says "{phrase}" but no ticket was created' if phrase else "no creation claim"


def _reply_commitment(ctx):
    phrase = output_check.commitment_phrase(ctx.reply or "")
    return bool(phrase), f'"{phrase}"' if phrase else "no commitment phrases"


def _reply_too_long(ctx, max_words=None):
    limit = int(max_words or policy()["thresholds"]["max_reply_words"])
    n = len((ctx.reply or "").split())
    return n > limit, f"{n} words (limit {limit})"


def _always(ctx):
    return True, "always"


CHECKS: dict[str, tuple[str, dict, list[str] | None, Callable]] = {
    "screen_flag": ("Input screen pattern matched", {"flag": "guardrails.yaml input_screen list"}, ["message"], _screen_flag),
    "redacted": ("D2 data was redacted from the message", {}, ["message"], _redacted),
    "classifier_unsure": ("Classifier unsure (E3 thresholds, F2)", {}, ["after_classifier"], _classifier_unsure),
    "unsure_count_over": ("Classifier unsure more than N times", {"times": "number"}, ["after_classifier"], _unsure_count_over),
    "handed_off": ("Conversation already handed off", {}, None, _handed_off),
    "ticket_already_created": ("A ticket was already created", {}, None, _ticket_already_created),
    "code_not_looked_up": ("Ticket's code not looked up yet", {}, ["before_create_ticket"], _code_not_looked_up),
    "duplicates_found": ("search_tickets found open tickets", {}, ["after_search_tickets"], _duplicates_found),
    "ticket_created": ("create_ticket returned a ticket ID", {}, ["after_create_ticket"], _ticket_created),
    "fields_missing": ("create_ticket reported missing fields", {}, ["after_create_ticket"], _fields_missing),
    "field_attempts_over": ("A missing field passed thresholds.max_field_attempts", {}, ["after_create_ticket"], _field_attempts_over),
    "status_args_missing": ("Ticket number or postal code missing", {}, ["before_get_status"], _status_args_missing),
    "reply_has_rule_ids": ("Reply contains policy rule IDs", {}, ["reply"], _reply_has_rule_ids),
    "reply_invented_id": ("Reply quotes an ID no tool returned", {}, ["reply"], _reply_invented_id),
    "reply_claims_ticket": ("Reply claims a ticket that wasn't created", {}, ["reply"], _reply_claims_ticket),
    "reply_commitment": ("Reply makes a commitment (date, priority, refund)", {}, ["reply"], _reply_commitment),
    "reply_too_long": ("Reply longer than max_reply_words", {"max_words": "number (default: thresholds)"}, ["reply"], _reply_too_long),
    "always": ("Always yes", {}, None, _always),
}

# name -> (label, params, ends the hook)
ACTIONS: dict[str, tuple[str, dict, bool]] = {
    "continue": ("Carry on", {}, False),
    "guide": ("Tell the model what to do next", {"say": "text; {placeholders} allowed"}, False),
    "flag": ("Log that this rule fired", {}, False),
    "strip_rule_ids": ("Remove rule IDs from the reply (D8)", {}, False),
    "reply": ("Reply with approved wording and end the turn", {"wording": "policy.yaml wording key"}, True),
    "handoff": ("Hand off to a person and end the turn",
                {"reason": "text", "prefix": "wording key shown first (optional)",
                 "handoff_rule": "rule ID on the handoff record (default: the node's)"}, True),
    "block": ("Refuse the tool call; tell the model why", {"message": "text; {placeholders} allowed"}, True),
}


# ------------------------------------------------------------------ engine

def normalize(doc):
    """YAML reads bare yes:/no: keys as booleans; turn them back into "yes"/"no"."""
    if not isinstance(doc, dict) or not isinstance(doc.get("nodes"), dict):
        return doc
    nodes = {}
    for nid, n in doc["nodes"].items():
        if isinstance(n, dict):
            n = {("yes" if k is True else "no" if k is False else k): v for k, v in n.items()}
        nodes[str(nid)] = n
    return {**doc, "nodes": nodes}


@config_cache
def tree() -> dict:
    return normalize(flow_config())


def run(hook: str, ctx: Ctx, trace: Callable[[dict], None] | None = None, doc: dict | None = None) -> Outcome:
    doc = normalize(doc) if doc else tree()
    nodes = doc.get("nodes") or {}
    node_id = (doc.get("hooks") or {}).get(hook)
    for _ in range(MAX_NODES_PER_RUN):
        if not node_id:
            return Outcome()
        node = nodes[node_id]
        step = {"hook": hook, "node": node_id, "title": node.get("title"), "rule": node.get("rule")}
        enabled = node.get("enabled", True)

        if "check" in node:
            if enabled:
                fn = CHECKS[node["check"]][3]
                hit, observed = fn(ctx, **(node.get("params") or {}))
                ctx.observed = observed
                branch = "yes" if hit else "no"
            else:
                hit, observed, branch = False, "node switched off", "skipped"
            if trace:
                trace({**step, "check": node["check"], "observed": observed, "branch": branch,
                       "next": node.get("yes" if hit else "no"), "fired": hit})
            node_id = node.get("yes" if hit else "no")
            continue

        action, params = node["action"], node.get("params") or {}
        if not enabled:
            if trace:
                trace({**step, "action": action, "branch": "skipped", "observed": "node switched off",
                       "next": node.get("next"), "fired": False})
            node_id = node.get("next")
            continue
        outcome = _act(action, params, node, ctx)
        if trace:
            trace({**step, "action": action, "branch": action, "next": node.get("next"),
                   "fired": action != "continue", "says": outcome.text or outcome.reason})
        if outcome.kind != "proceed":
            return outcome
        node_id = node.get("next")
    raise RuntimeError(f"flow hook {hook!r} visited more than {MAX_NODES_PER_RUN} nodes (cycle?)")


def _act(action: str, params: dict, node: dict, ctx: Ctx) -> Outcome:
    rule = node.get("rule")
    if action == "guide":
        text = _fmt(params.get("say", ""), ctx)
        ctx.guidance.append(text)
        return Outcome(text=text)
    if action == "strip_rule_ids":
        ctx.reply, _ = output_check.strip_rule_ids(ctx.reply or "")
        return Outcome()
    if action == "reply":
        return Outcome("reply", rule, text=_fmt(policy()["wording"][params["wording"]], ctx))
    if action == "handoff":
        return Outcome("handoff", rule, reason=_fmt(params.get("reason") or node.get("title") or "", ctx),
                       handoff_rule=params.get("handoff_rule") or rule, prefix=params.get("prefix"))
    if action == "block":
        return Outcome("block", rule, text=_fmt(params.get("message", "not allowed"), ctx))
    return Outcome()   # continue, flag


# ------------------------------------------------------------------ validation

def validate(doc: Any, pol: dict | None = None, guards: dict | None = None) -> list[str]:
    """Problems that would stop the tree from running or drop a hard rule. Empty = OK."""
    pol = pol if pol is not None else policy()
    guards = guards if guards is not None else guardrails_config()
    doc = normalize(doc)
    if not isinstance(doc, dict):
        return ["flow.yaml must be a mapping with hooks: and nodes:"]
    hooks, nodes = doc.get("hooks") or {}, doc.get("nodes") or {}
    if not isinstance(hooks, dict) or not isinstance(nodes, dict):
        return ["flow.yaml: hooks and nodes must both be mappings"]
    errors = []
    rules, wording = pol.get("rules") or {}, pol.get("wording") or {}
    flags = set((guards.get("input_screen") or {}).keys())

    for hook, start in hooks.items():
        if hook not in HOOKS:
            errors.append(f"hook {hook!r} doesn't exist; available: {', '.join(HOOKS)}")
        if start and start not in nodes:
            errors.append(f"hook {hook!r} starts at {start!r}, which isn't a node")

    for nid, n in nodes.items():
        where = f"node {nid!r}"
        if not isinstance(n, dict):
            errors.append(f"{where} must be a mapping")
            continue
        if ("check" in n) == ("action" in n):
            errors.append(f"{where} needs exactly one of check: or action:")
            continue
        if n.get("rule") and n["rule"] not in rules:
            errors.append(f"{where} cites rule {n['rule']!r}, which isn't in policy.yaml rules")
        params = n.get("params") or {}
        if not isinstance(params, dict):
            errors.append(f"{where}: params must be a mapping")
            continue
        targets = [n.get("yes"), n.get("no")] if "check" in n else [n.get("next")]
        for t in targets:
            if t and t not in nodes:
                errors.append(f"{where} points to {t!r}, which isn't a node")
        if "check" in n:
            if n["check"] not in CHECKS:
                errors.append(f"{where}: unknown check {n['check']!r}")
            elif n["check"] == "screen_flag" and params.get("flag") not in flags:
                errors.append(f"{where}: flag must be one of the input_screen lists ({', '.join(sorted(flags))})")
        else:
            a = n["action"]
            if a not in ACTIONS:
                errors.append(f"{where}: unknown action {a!r}")
                continue
            if ACTIONS[a][2] and n.get("next"):
                errors.append(f"{where}: {a} ends the hook, so it can't have next:")
            if a == "reply" and params.get("wording") not in wording:
                errors.append(f"{where}: wording must be a key under policy.yaml wording")
            if a == "handoff" and params.get("prefix") and params["prefix"] not in wording:
                errors.append(f"{where}: prefix must be a key under policy.yaml wording")
            if a == "handoff" and params.get("handoff_rule") and params["handoff_rule"] not in rules:
                errors.append(f"{where}: handoff_rule {params['handoff_rule']!r} isn't in policy.yaml rules")
            if a == "guide" and not params.get("say"):
                errors.append(f"{where}: guide needs say:")
    if errors:
        return errors

    for hook, start in hooks.items():
        path = _cycle(start, nodes)
        if path:
            errors.append(f"hook {hook!r} loops: {' -> '.join(path)}")
    if errors:
        return errors

    for rule, hook in HARD_RULES.items():
        live = reachable(hooks.get(hook), nodes, live_only=True)
        if not any(nodes[n].get("rule") == rule and "action" in nodes[n] for n in live):
            errors.append(f"{rule} is a [hard] rule: the {hook!r} tree needs a switched-on action that cites it, "
                          "reachable through switched-on steps")
    return errors


def reachable(start: str | None, nodes: dict, live_only: bool = False) -> list[str]:
    """Nodes a hook can reach. live_only: as the engine runs it, so a switched-off
    decision only leads to its "no" branch and a switched-off action isn't counted."""
    seen, visited, stack = [], set(), [start] if start else []
    while stack:
        nid = stack.pop()
        if nid in visited or nid not in nodes:
            continue
        visited.add(nid)
        n = nodes[nid]
        off = live_only and n.get("enabled", True) is False
        if not off:
            seen.append(nid)
        if off and "check" in n:
            stack += [n["no"]] if n.get("no") else []
        else:
            stack += [t for t in (n.get("yes"), n.get("no"), n.get("next")) if t]
    return seen


def _cycle(start, nodes, path=()):
    if not start or start not in nodes:
        return None
    if start in path:
        return [*path[path.index(start):], start]
    n = nodes[start]
    for t in (n.get("yes"), n.get("no"), n.get("next")):
        found = _cycle(t, nodes, (*path, start))
        if found:
            return found
    return None


def catalogue() -> dict:
    """What /admin's tree editor offers."""
    return {
        "hooks": HOOKS,
        "hard_rules": HARD_RULES,
        "checks": {k: {"label": v[0], "params": v[1], "hooks": v[2]} for k, v in CHECKS.items()},
        "actions": {k: {"label": v[0], "params": v[1], "ends": v[2]} for k, v in ACTIONS.items()},
    }
