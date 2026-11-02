"""/admin API: interaction walkthrough, browser edits of config/, knowledge base."""
import pytest
from fastapi.testclient import TestClient

from src import config
from src.agent import orchestrator
from src.web.server import app
from conftest import tool_call

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_config():
    config.reload()
    yield
    config.reload()


def put(path, content, **kw):
    return client.put("/api/admin/file", params={"path": path}, json={"content": content, **kw})


# ---------------------------------------------------------------- walkthrough

def test_walkthrough_shows_every_step_of_a_conversation(llm, fresh_store):
    llm([tool_call("policy_lookup", {"code": "SW-BIN"})],
        [tool_call("create_ticket", {"code": "SW-BIN", "fields": {
            "address": "12 Oak Street, G1A 1A1", "bin_type": "recycling", "damage_type": "cracked lid"}})],
        "Done.")
    r = client.post("/api/chat", json={"message": "My recycling bin lid is cracked at 12 Oak Street",
                                       "provider": "ollama", "model": "m"})
    iid = r.json()["interaction_id"]

    listed = client.get("/api/admin/interactions").json()["interactions"]
    assert listed[0]["interaction_id"] == iid and listed[0]["tickets"] == 1

    d = client.get(f"/api/admin/interactions/{iid}").json()
    steps = [e["step"] for e in d["events"]]
    assert steps[0] == "input_screen" and steps[-1] == "reply"
    assert "tool:policy_lookup" in steps and "tool:create_ticket" in steps
    created = next(e for e in d["events"] if e["step"] == "tool:create_ticket")
    assert created["data"]["result"]["ticket_id"].startswith("311-")
    assert d["tickets"][0]["ticket_id"] == created["data"]["result"]["ticket_id"]
    assert "12 Oak" not in str(d["events"])                       # D6: masked, as in the logs
    assert "12 Oak" not in str(d["tickets"])


def test_rules_endpoint_serves_policy_rule_text():
    r = client.get("/api/admin/rules").json()
    assert r["rules"]["E1"]["hard"] is True and "SW-BIN" in r["catalogue"]


# ---------------------------------------------------------------- config files

def test_edit_goes_live_without_redeploy_and_keeps_history(fresh_store):
    text = client.get("/api/admin/file", params={"path": "app.yaml"}).json()["content"]
    assert put("app.yaml", text.replace("City of Gilead 311", "Gilead 311 (test)"), author="Sam", note="rename").status_code == 200
    assert client.get("/api/config").json()["site"]["title"] == "Gilead 311 (test)"
    files = {f["path"]: f for f in client.get("/api/admin/files").json()["files"]}
    assert files["app.yaml"]["edited"] and files["app.yaml"]["updated_by"] == "Sam"

    assert client.delete("/api/admin/file", params={"path": "app.yaml"}).status_code == 200
    assert client.get("/api/config").json()["site"]["title"] == "City of Gilead 311"
    revs = client.get("/api/admin/revisions", params={"path": "app.yaml"}).json()["revisions"]
    assert [r["note"] for r in revs] == ["reverted to the repo version", "rename"]


def test_guardrail_edit_applies_to_the_next_message(llm, fresh_store):
    text = client.get("/api/admin/file", params={"path": "guardrails.yaml"}).json()["content"]
    assert put("guardrails.yaml", text.replace("emergency:\n", "emergency:\n    - '\\bsinkhole\\b'\n", 1)).status_code == 200
    llm()   # no model call expected: E1 answers directly
    r = client.post("/api/chat", json={"message": "there's a sinkhole on Main St", "provider": "ollama", "model": "m"})
    assert r.json()["rule_id"] == "E1"


@pytest.mark.parametrize("path,breaker,expect", [
    ("policy.yaml", lambda t: t + "\n  bad: [unclosed", "not valid YAML"),
    ("policy.yaml", lambda t: t.replace("wording:", "wordings:"), "wording"),
    ("guardrails.yaml", lambda t: t.replace("'\\bfire\\b'", "'(unclosed'"), "bad regular expression"),
    ("prompts/agent/system_v1.md", lambda t: t + "\n{not_a_placeholder}", "not_a_placeholder"),
    ("app.yaml", lambda t: t.replace("system_v1.md", "missing.md"), "doesn't exist"),
])
def test_edits_that_would_break_the_agent_are_refused(fresh_store, path, breaker, expect):
    text = client.get("/api/admin/file", params={"path": path}).json()["content"]
    r = put(path, breaker(text))
    assert r.status_code == 422 and expect in " ".join(r.json()["detail"]["errors"])
    assert not fresh_store.configs                                  # nothing saved


def test_new_prompt_file_can_be_created_and_switched_to(fresh_store):
    base = client.get("/api/admin/file", params={"path": "prompts/agent/system_v1.md"}).json()["content"]
    assert put("prompts/agent/system_v2.md", "v2 MARKER\n" + base).status_code == 200
    app_text = client.get("/api/admin/file", params={"path": "app.yaml"}).json()["content"]
    assert put("app.yaml", app_text.replace("system_v1.md", "system_v2.md")).status_code == 200
    assert orchestrator.system_prompt().startswith("v2 MARKER")


def test_only_config_files_are_editable():
    for path in ("../.env", "prompts/../../.env", "secrets.yaml", "prompts/agent/x.py"):
        assert put(path, "x").status_code == 400


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
