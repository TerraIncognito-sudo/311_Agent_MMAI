"""/admin API: traced walkthrough, the interaction tree, config in Supabase, knowledge base."""
import copy

import pytest
from fastapi.testclient import TestClient

from src.agent import orchestrator
from src.web.server import app
from conftest import tool_call

client = TestClient(app)


def put(path, content, **kw):
    return client.put("/api/admin/file", params={"path": path}, json={"content": content, **kw})


def get_text(path):
    return client.get("/api/admin/file", params={"path": path}).json()["content"]


def chat(message, iid=None):
    return client.post("/api/chat", json={"interaction_id": iid, "message": message, "provider": "ollama", "model": "m"})


# ---------------------------------------------------------------- traced walkthrough

def test_walkthrough_traces_every_decision(llm, fresh_store):
    llm([tool_call("policy_lookup", {"code": "SW-BIN"})],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": {
            "location": "12 Oak Street, G1A 1A1", "bin_type": "recycling", "damage_type": "cracked lid"}})],
        "Done.")
    iid = chat("My recycling bin lid is cracked at 12 Oak Street").json()["interaction_id"]

    listed = client.get("/api/admin/interactions").json()["interactions"]
    assert listed[0]["interaction_id"] == iid and listed[0]["tickets"] == 1

    d = client.get(f"/api/admin/interactions/{iid}").json()
    steps = [e["step"] for e in d["events"]]
    assert steps[0] == "input_screen" and steps[-1] == "reply"
    trace = [e["data"] for e in d["events"] if e["step"] == "flow"]
    by_node = {t["node"]: t for t in trace}
    # message tree: every screen decision taken, with what it saw
    assert by_node["emergency"]["branch"] == "no" and "no emergency pattern" in by_node["emergency"]["observed"]
    assert by_node["to_agent"]["hook"] == "message"
    # before creating: A8 and A2 checks ran and passed
    assert by_node["one_ticket"]["branch"] == "no" and by_node["looked_up"]["branch"] == "no"
    # after creating: the tree told the model how to confirm (M3)
    assert by_node["ticket_created"]["branch"] == "yes" and "311-" in by_node["confirm_ticket"]["says"]
    created = next(e for e in d["events"] if e["step"] == "tool:create_ticket")
    seen_by_model = [m["content"] for m in orchestrator._SESSIONS[iid].messages if m["role"] == "tool"]
    assert any('"guidance"' in c and "approved ticket_created wording" in c for c in seen_by_model)
    assert d["tickets"][0]["ticket_id"] == created["data"]["result"]["ticket_id"]
    assert "12 Oak" not in str(d["events"]) and "12 Oak" not in str(d["tickets"])   # D6


def test_fired_rules_are_logged_with_the_decision(llm, fresh_store):
    llm()
    iid = chat("there's a gas smell in my kitchen").json()["interaction_id"]
    events = client.get(f"/api/admin/interactions/{iid}").json()["events"]
    e1 = next(e for e in events if e["step"] == "flow" and e["data"]["node"] == "emergency")
    assert e1["rule_id"] == "E1" and e1["outcome"] == "yes" and "gas" in e1["data"]["observed"]


# ---------------------------------------------------------------- interaction tree

def flow_doc():
    return copy.deepcopy(client.get("/api/admin/flow").json()["doc"])


def save_flow(doc):
    return client.put("/api/admin/flow", json={"doc": doc, "author": "Sam", "note": "test"})


def test_flow_endpoint_serves_tree_and_vocabulary():
    r = client.get("/api/admin/flow").json()
    assert r["errors"] == [] and r["doc"]["hooks"]["message"] == "redacted"
    assert "screen_flag" in r["catalogue"]["checks"] and r["catalogue"]["actions"]["handoff"]["ends"]
    assert r["doc"]["nodes"]["emergency"]["no"] == "distress"       # yes/no keys survive YAML


def test_tree_edit_changes_behaviour_at_once(llm, fresh_store):
    doc = flow_doc()
    doc["nodes"]["distress"]["enabled"] = False
    assert save_flow(doc).status_code == 200
    llm("I'm sorry to hear that. How can I help?")
    out = chat("I want to end my life").json()
    assert out["rule_id"] is None and not fresh_store.handoffs                  # E8 node switched off
    skipped = [e for e in fresh_store.events if e["step"] == "flow" and e["outcome"] == "skipped"]
    assert skipped and "distress" in skipped[0]["text"]


def test_new_tree_step_runs(llm, fresh_store):
    doc = flow_doc()
    doc["nodes"]["potholes_to_person"] = {"title": "Pothole? Send to a person", "rule": "E2",
                                          "check": "always", "yes": "pothole_handoff"}
    doc["nodes"]["pothole_handoff"] = {"title": "Hand off", "rule": "E2", "action": "handoff",
                                       "params": {"reason": "potholes go to a person in this trial"}}
    doc["hooks"]["after_policy_lookup"] = "potholes_to_person"
    assert save_flow(doc).status_code == 200
    llm([tool_call("policy_lookup", {"code": "RD-POT"})])
    out = chat("pothole on King St").json()
    assert out["rule_id"] == "E2" and fresh_store.handoffs[0]["reason"] == "potholes go to a person in this trial"


@pytest.mark.parametrize("breaker,expect", [
    (lambda d: d["nodes"].pop("call_911") and d["nodes"]["emergency"].pop("yes"), "E1 is a [hard] rule"),
    (lambda d: d["nodes"]["emergency"].update(enabled=False), "E1 is a [hard] rule"),
    (lambda d: d["nodes"]["to_agent"].update(next="redacted"), "loops"),
    (lambda d: d["nodes"]["emergency"].update(no="nowhere"), "isn't a node"),
    (lambda d: d["nodes"]["emergency"].update(check="made_up"), "unknown check"),
    (lambda d: d["nodes"]["call_911"]["params"].update(wording="nope"), "wording must be"),
    (lambda d: d["hooks"].update(after_lunch="emergency"), "doesn't exist"),
])
def test_tree_edits_that_would_break_the_agent_are_refused(fresh_store, breaker, expect):
    doc = flow_doc()
    breaker(doc)
    r = save_flow(doc)
    assert r.status_code == 422 and expect in " ".join(r.json()["detail"]["errors"])
    assert not fresh_store.revisions


# ---------------------------------------------------------------- config files (Supabase only)

def test_edit_goes_live_and_keeps_history(fresh_store):
    assert put("app.yaml", get_text("app.yaml").replace("City of Gilead 311", "Gilead 311 (test)"),
               author="Sam", note="rename").status_code == 200
    assert client.get("/api/config").json()["site"]["title"] == "Gilead 311 (test)"
    files = {f["path"]: f for f in client.get("/api/admin/files").json()["files"]}
    assert files["app.yaml"]["updated_by"] == "Sam" and files["flow.yaml"]["in_use"]
    revs = client.get("/api/admin/revisions", params={"path": "app.yaml"}).json()["revisions"]
    assert revs[0]["note"] == "rename"


def test_guardrail_edit_applies_to_the_next_message(llm, fresh_store):
    text = get_text("guardrails.yaml")
    assert put("guardrails.yaml", text.replace("emergency:\n", "emergency:\n    - '\\bsinkhole\\b'\n", 1)).status_code == 200
    llm()   # no model call expected: E1 answers directly
    assert chat("there's a sinkhole on Main St").json()["rule_id"] == "E1"


def test_dashboard_edits_are_picked_up_by_the_sync(fresh_store):
    from src.config import app_config, refresh
    app_config()
    row = fresh_store.configs["app.yaml"]
    row["content"] = row["content"].replace("City of Gilead 311", "Edited in the dashboard")
    assert refresh() and app_config()["site"]["title"] == "Edited in the dashboard"


@pytest.mark.parametrize("path,breaker,expect", [
    ("policy.yaml", lambda t: t + "\n  bad: [unclosed", "not valid YAML"),
    ("policy.yaml", lambda t: t.replace("wording:", "wordings:"), "wording"),
    ("policy.yaml", lambda t: t.replace("  E1: {", "  X1: {"), "E1"),
    ("guardrails.yaml", lambda t: t.replace("'\\bsuicid'", "'(unclosed'"), "bad regular expression"),
    ("prompts/agent/system_v1.md", lambda t: t + "\n{not_a_placeholder}", "not_a_placeholder"),
    ("app.yaml", lambda t: t.replace("system_v1.md", "missing.md"), "doesn't exist"),
])
def test_edits_that_would_break_the_agent_are_refused(fresh_store, path, breaker, expect):
    r = put(path, breaker(get_text(path)))
    assert r.status_code == 422 and expect in " ".join(r.json()["detail"]["errors"])
    assert not fresh_store.revisions                                  # nothing saved


def test_new_prompt_file_can_be_created_switched_to_and_deleted(fresh_store):
    base = get_text("prompts/agent/system_v1.md")
    assert put("prompts/agent/system_v2.md", "v2 MARKER\n" + base).status_code == 200
    assert put("app.yaml", get_text("app.yaml").replace("system_v1.md", "system_v2.md")).status_code == 200
    assert orchestrator.system_prompt().startswith("v2 MARKER")
    assert client.delete("/api/admin/file", params={"path": "prompts/agent/system_v2.md"}).status_code == 422  # in use
    assert client.delete("/api/admin/file", params={"path": "prompts/agent/system_v1.md"}).status_code == 200


def test_only_config_files_are_editable():
    for path in ("../.env", "prompts/../../.env", "secrets.yaml", "prompts/agent/x.py"):
        assert put(path, "x").status_code == 400


def test_without_supabase_config_the_site_says_so(fresh_store):
    from src import config
    fresh_store.fail.add("list_config")
    config.reload()
    assert chat("hello").status_code == 503
    assert client.get("/api/health").json()["config"] == "unavailable"


def test_admin_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("ADMIN_ENABLED", "0")
    assert client.get("/admin").status_code == 404
    assert client.get("/api/admin/files").status_code == 404


# ---------------------------------------------------------------- knowledge base

def test_kb_document_created_in_admin_is_searchable_at_once(fresh_store):
    from src.tools.knowledge import kb_search
    r = client.post("/api/admin/kb", json={"title": "Parks", "content": "Park washrooms open May to October."})
    doc = r.json()["document"]
    assert doc["source"] == "parks" and kb_search("park washrooms")["passages"]

    assert client.post("/api/admin/kb", json={"title": "Parks", "content": "dup"}).status_code == 409
    client.put(f"/api/admin/kb/{doc['id']}", json={"title": "Parks", "content": doc["content"], "active": False})
    assert not kb_search("park washrooms")["passages"]
    client.delete(f"/api/admin/kb/{doc['id']}")
    assert client.get("/api/admin/kb").json()["documents"] == []


# ---------------------------------------------------------------- tickets and handoffs

def _ticket_conversation(llm):
    llm([tool_call("policy_lookup", {"code": "SW-BIN"})],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": {
            "location": "12 Oak Street, G1A 1A1", "bin_type": "recycling", "damage_type": "cracked lid"}})],
        "Done.")
    return chat("My recycling bin lid is cracked at 12 Oak Street").json()["interaction_id"]


def test_ticket_is_listed_linked_and_tracked(llm, fresh_store):
    iid = _ticket_conversation(llm)
    r = client.get("/api/admin/tickets").json()
    t = r["tickets"][0]
    assert t["interaction_id"] == iid and r["counts"] == {"open": 1}
    assert "12 Oak" not in str(r) and "contact" not in t                              # D6, D1

    tid = t["ticket_id"]
    assert client.post(f"/api/admin/tickets/{tid}/updates", json={}).status_code == 422   # nothing to record
    assert client.post(f"/api/admin/tickets/{tid}/updates", json={"status": "done"}).status_code == 422
    ok = client.post(f"/api/admin/tickets/{tid}/updates",
                     json={"status": "in_progress", "note": "crew booked", "author": "Sam"})
    assert ok.status_code == 200
    d = client.get(f"/api/admin/tickets/{tid}").json()
    assert d["ticket"]["status"] == "in_progress" and d["required_fields"] == ["location", "bin_type", "damage_type"]
    u = d["updates"][0]
    assert (u["old_status"], u["new_status"], u["note"], u["author"]) == ("open", "in_progress", "crew booked", "Sam")
    assert client.get("/api/admin/tickets", params={"status": "open"}).json()["tickets"] == []
    assert client.get("/api/admin/tickets", params={"q": tid[-3:]}).json()["tickets"]


def test_handoff_is_listed_and_tracked(llm, fresh_store):
    llm([tool_call("handoff", {"rule_id": "E2", "reason": "resident injured by bin"})])
    chat("The bin fell on me and hurt my foot")
    r = client.get("/api/admin/handoffs").json()
    o = r["handoffs"][0]
    assert o["rule_id"] == "E2" and o["status"] == "pending" and r["counts"] == {"pending": 1}
    assert client.get("/api/admin/handoffs", params={"rule_id": "E7"}).json()["handoffs"] == []
    client.post(f"/api/admin/handoffs/{o['ref_id']}/updates", json={"status": "resolved", "note": "called back"})
    d = client.get(f"/api/admin/handoffs/{o['ref_id']}").json()
    assert d["handoff"]["status"] == "resolved" and d["handoff"]["transcript"]
    assert d["updates"][0]["new_status"] == "resolved"
    assert client.get("/api/admin/handoffs/HO-0000-000000").status_code == 404


# ---------------------------------------------------------------- cache busting

def test_pages_point_at_versioned_static_files():
    r = client.get("/admin")
    assert r.headers["cache-control"] == "no-cache"
    assert '/static/admin.js?v=' in r.text and '/static/admin.css?v=' in r.text
    assert '/static/app.js?v=' in client.get("/").text


# ---------------------------------------------------------------- analytics

def test_analytics_counts_requests_outcomes_and_cost(llm, fresh_store):
    llm([tool_call("policy_lookup", {"code": "SW-BIN"})],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": {
            "location": "12 Oak Street, G1A 1A1", "bin_type": "recycling", "damage_type": "cracked lid"}})],
        "Done.",
        [tool_call("handoff", {"rule_id": "E3", "reason": "can't tell which service"})])
    chat("My recycling bin lid is cracked at 12 Oak Street")
    chat("Something is wrong on my street")
    chat("there's a gas smell in my kitchen")   # E1: sent to 911 before any model call

    d = client.get("/api/admin/analytics", params={"bucket": "hour", "periods": 24, "tz": "America/Toronto"}).json()
    t = d["totals"]
    assert len(d["rows"]) == 24 and d["tz"] == "America/Toronto"
    assert (t["requests"], t["ticket"], t["handoff"], t["answered"], t["emergencies"]) == (3, 1, 1, 1, 1)
    assert (t["tickets"], t["handoffs"], t["turns"], t["model_calls"]) == (1, 1, 3, 4)
    assert d["rows"][-1]["requests"] == 3   # all in the current hour
    assert d["latency"]["turns"] == 3 and d["latency"]["p95_ms"] is not None
    assert d["codes"][0]["code"] == "SW-BIN" and d["handoff_rules"][0]["rule_id"] == "E3"
    # model speed: two replies came from the model, the 911 one from the tree alone
    speed = {r["model"]: r for r in d["speed"]}
    model = next(k for k in speed if k != "tree")
    assert speed[model]["replies"] == 2 and speed[model]["calls"] == 4 and speed[model]["calls_per_reply"] == 2
    assert speed[model]["call_p50_ms"] == 1 and speed[model]["tokens_per_s"] == 5000   # 5 tokens in 1 ms
    assert speed["tree"]["replies"] == 1 and speed["tree"]["calls"] == 0 and d["speed"][-1]["model"] == "tree"
    assert isinstance(d["handoff_rules"][0]["text"], str)
    # defaults: $45/h, 312 s per manual request and per handoff review, free hosting and Ollama
    assert t["review_cost"] == pytest.approx(3.9) and t["manual_cost"] == pytest.approx(11.7)
    assert t["total_cost"] == pytest.approx(3.9) and t["savings"] == pytest.approx(7.8)
    assert "labour_rate_per_hour" in d["cost_defaults"]

    assert client.get("/api/admin/analytics", params={"bucket": "week"}).status_code == 422
    assert len(client.get("/api/admin/analytics", params={"bucket": "month", "periods": 999}).json()["rows"]) == 36


def test_cost_assumptions_are_saved_into_app_yaml(llm, fresh_store):
    r = client.put("/api/admin/costs", json={"costs": {"labour_rate_per_hour": 60, "hosting_per_month": 30}, "author": "g"})
    assert r.status_code == 200, r.text
    text = get_text("app.yaml")
    assert "# Site and runtime settings" in text and "labour_rate_per_hour: 60" in text   # other comments kept
    client.put("/api/admin/costs", json={"costs": {"currency": "CAD"}})
    text = get_text("app.yaml")
    assert text.count("costs:") == 1 and "labour_rate_per_hour: 60" in text and 'currency: "CAD"' in text
    d = client.get("/api/admin/analytics").json()
    assert d["costs"]["hosting_per_month"] == 30 and d["cost_defaults"] == []
    assert d["totals"]["hosting_cost"] > 0

    for bad in ({"labour_rate_per_hour": -1}, {"labour_rate_per_hour": "lots"}, {"tip_jar": 1}):
        r = client.put("/api/admin/costs", json={"costs": bad})
        assert r.status_code == 422, bad
    assert put("app.yaml", text + "\n").status_code == 200
    assert put("app.yaml", text.replace("labour_rate_per_hour: 60", "labour_rate_per_hour: -5")).status_code == 422


def test_analytics_periods_follow_local_time():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from src.web import analytics as stats

    tz = ZoneInfo("America/Toronto")
    now = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)   # DST ended Nov 1
    edges = stats.window("day", 3, tz, now)
    assert [e.day for e in edges] == [31, 1, 2, 3]
    assert edges[2].timestamp() - edges[1].timestamp() == 25 * 3600   # the long day
    months = stats.window("month", 3, tz, datetime(2027, 1, 15, tzinfo=timezone.utc))
    assert [(e.year, e.month) for e in months] == [(2026, 11), (2026, 12), (2027, 1), (2027, 2)]

    events = [
        {"interaction_id": "a", "step": "input_screen", "created_at": "2026-11-01T03:30:00+00:00"},   # Oct 31 local
        {"interaction_id": "a", "step": "reply", "created_at": "2026-11-01T03:30:02+00:00"},
        {"interaction_id": "b", "step": "input_screen", "created_at": "2026-11-02T12:00:00+00:00"},
        {"interaction_id": "c", "step": "input_screen", "created_at": "2026-10-01T12:00:00+00:00"},   # before the window
    ]
    d = stats.summarize(events, [], "day", edges, now, stats.DEFAULT_COSTS)
    assert [r["requests"] for r in d["rows"]] == [1, 0, 1]
    assert d["latency"]["p50_ms"] == 2000


# ---------------------------------------------------------------- re-runs

def _rerun(iid, model="other", provider="ollama", **headers):
    from src.agent import rerun
    r = client.post(f"/api/admin/interactions/{iid}/rerun", json={"provider": provider, "model": model}, headers=headers)
    if r.status_code == 200:
        rerun.get(r.json()["job_id"]).thread.join(10)
        return rerun.get(r.json()["job_id"])
    return r


def test_rerun_replays_a_conversation_on_another_model_without_touching_records(llm, fresh_store):
    llm([tool_call("policy_lookup", {"code": "SW-BIN"})],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": {
            "location": "12 Oak Street, G1A 1A1", "bin_type": "recycling", "damage_type": "cracked lid"}})],
        "Done.")
    iid = chat("My recycling bin lid is cracked at 12 Oak Street").json()["interaction_id"]
    assert len(fresh_store.tickets) == 1

    fake = llm([tool_call("policy_lookup", {"code": "SW-BIN"})],
               [tool_call("search_tickets", {"location": "12 Oak Street", "code": "SW-BIN"})],
               [tool_call("create_ticket", {"code": "SW-BIN", "fields": {
                   "location": "100 Oak Street, G1A 1A1", "bin_type": "recycling", "damage_type": "cracked lid"}})],
               "Filed.")
    job = _rerun(iid)
    assert job.status == "done" and job.done == 1 and job.model == "ollama:other", job.error
    # the masked street number was replaced with a stand-in for the model
    assert "100 Oak Street" in fake.calls[0][-1]["content"]
    # nothing written to City records; the original's own ticket wasn't offered as a duplicate
    assert len(fresh_store.tickets) == 1 and not fresh_store.handoffs
    evs = client.get(f"/api/admin/interactions/{job.rerun_id}").json()["events"]
    search = next(e for e in evs if e["step"] == "tool:search_tickets")
    assert search["data"]["result"]["tickets"] == []   # the original ticket at 12 Oak Street is hidden
    created = next(e for e in evs if e["step"] == "tool:create_ticket")
    assert created["data"]["result"]["ticket_id"].endswith("900001")

    listed = {r["interaction_id"]: r for r in client.get("/api/admin/interactions").json()["interactions"]}
    assert listed[job.rerun_id]["rerun_of"] == iid and listed[iid]["rerun_of"] is None

    d = client.get("/api/admin/reruns").json()
    g = d["groups"][0]
    assert g["original"] == iid and [r["model"] for r in g["runs"]] == ["ollama:m", "ollama:other"]
    orig, run = g["runs"]
    assert not orig["rerun"] and run["rerun"] and run["outcome"] == orig["outcome"] == "ticket"
    assert [t["name"] for t in run["turns"][0]["tools"]] == ["policy_lookup", "search_tickets", "create_ticket"]
    assert run["turns"][0]["same_tools"] is False and run["same_turns"] == 0 and run["same_outcome"]
    assert run["calls"] == 4 and run["tokens_in"] == 40 and run["turns"][0]["reply"] == "Filed."
    assert {m["model"] for m in d["by_model"]} == {"ollama:m", "ollama:other"}

    # test traffic stays out of usage and cost
    t = client.get("/api/admin/analytics", params={"bucket": "hour", "periods": 2}).json()["totals"]
    assert t["requests"] == 1 and t["model_calls"] == 3

    # re-running the re-run replays the original
    llm("Hello again.")
    again = _rerun(job.rerun_id, model="third")
    assert again.original == iid and again.status == "done"


def test_rerun_refuses_bad_requests(llm, fresh_store):
    llm("Hi.")
    iid = chat("hello").json()["interaction_id"]
    assert _rerun(iid, provider="openrouter", model="x/y").status_code == 400    # no key
    assert _rerun("no-such-conversation").status_code == 409
