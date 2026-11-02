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
            "address": "12 Oak Street, G1A 1A1", "bin_type": "recycling", "damage_type": "cracked lid"}})],
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
    ("guardrails.yaml", lambda t: t.replace("'\\bfire\\b'", "'(unclosed'"), "bad regular expression"),
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
