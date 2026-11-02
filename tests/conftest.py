import os

os.environ["USE_MEMORY_STORE"] = "1"   # tests never touch Supabase; config comes from tests/fixtures/config

import json

import pytest

from src import config
from src.agent import orchestrator
from src.models.llm import LLMResponse, LLMSelection
from src.tools import store as store_mod


@pytest.fixture(autouse=True)
def fresh_store():
    store_mod.get_store.cache_clear()
    config.reload()   # each test starts from the fixture config
    orchestrator._SESSIONS.clear()
    yield store_mod.get_store()
    store_mod.get_store.cache_clear()
    config.reload()


def tool_call(name, args, call_id=None):
    return {"id": call_id or f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptedLLM:
    """Stands in for src.models.llm.chat: returns scripted assistant messages in order."""

    def __init__(self, *messages):
        self.messages = list(messages)
        self.calls = []

    def __call__(self, sel, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        item = self.messages.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            item = {"role": "assistant", "content": item}
        elif isinstance(item, list):
            item = {"role": "assistant", "content": "", "tool_calls": item}
        return LLMResponse(message=item, tokens_in=10, tokens_out=5, cost_usd=0.0, latency_ms=1)


@pytest.fixture
def llm(monkeypatch):
    def install(*messages):
        fake = ScriptedLLM(*messages)
        monkeypatch.setattr(orchestrator, "chat", fake)
        return fake
    return install


@pytest.fixture
def sel():
    return LLMSelection(provider="ollama", model="test-model")
