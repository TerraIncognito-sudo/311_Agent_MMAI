"""Web API, including the rule that a user's OpenRouter key is never kept."""
from fastapi.testclient import TestClient

from src.agent import orchestrator
from src.web.server import app

client = TestClient(app)


def test_config_has_city_title():
    r = client.get("/api/config")
    assert r.status_code == 200 and "Gilead" in r.json()["site"]["title"]


def test_openrouter_key_used_but_not_stored(llm, fresh_store):
    seen = {}
    fake = llm("Hello, I'm an automated assistant.")

    def spy(sel, messages, tools=None):
        seen["key"] = sel.api_key
        return fake(sel, messages, tools)

    orchestrator.chat = spy  # monkeypatched back by the llm fixture
    key = "sk-or-test-SECRET-123"
    r = client.post("/api/chat", headers={"X-OpenRouter-Key": key},
                    json={"message": "hi", "provider": "openrouter", "model": "some/model"})
    assert r.status_code == 200 and seen["key"] == key
    session = orchestrator._SESSIONS[r.json()["interaction_id"]]
    assert key not in repr(session.__dict__)
    assert key not in repr(fresh_store.events) and key not in r.text


def test_openrouter_without_key_is_rejected(fresh_store):
    r = client.post("/api/chat", json={"message": "hi", "provider": "openrouter", "model": "some/model"})
    assert r.status_code == 400 and "key" in r.json()["detail"]
