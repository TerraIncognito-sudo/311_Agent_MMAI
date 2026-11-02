"""LLM backbone: one chat() call for every provider (owner: C for models, D for the agent).

Both providers speak the OpenAI chat-completions format with tool calling:
  ollama      -> {OLLAMA_BASE_URL}/v1/chat/completions (local server, no key)
  openrouter  -> {OPENROUTER_BASE_URL}/chat/completions (user's own key)

The OpenRouter key arrives with each request from the browser and lives only
in the LLMSelection for that request: it is never stored, logged or returned.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import httpx

from src.config import app_config, env

THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(RuntimeError):
    """retryable=False means a setup problem (bad key, unknown model, no tool
    support) that the person choosing the model must fix, not an outage."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class LLMSelection:
    provider: str                 # "ollama" | "openrouter"
    model: str
    api_key: str | None = field(default=None, repr=False)   # never printed

    def validate(self) -> None:
        if self.provider not in app_config()["llm"]["providers"]:
            raise LLMError(f"unknown provider {self.provider!r}", retryable=False)
        if not self.model:
            raise LLMError("no model selected", retryable=False)
        if self.provider == "openrouter" and not self.api_key:
            raise LLMError("an OpenRouter API key is required", retryable=False)

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class LLMResponse:
    message: dict                 # assistant message (content and/or tool_calls)
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    latency_ms: int


def _endpoint(sel: LLMSelection) -> tuple[str, dict]:
    if sel.provider == "ollama":
        return f"{env('OLLAMA_BASE_URL', 'http://localhost:11434')}/v1/chat/completions", {}
    headers = {"Authorization": f"Bearer {sel.api_key}", "X-Title": env("OPENROUTER_APP_NAME", "Gilead 311 Agent")}
    if env("OPENROUTER_APP_URL"):
        headers["HTTP-Referer"] = env("OPENROUTER_APP_URL")
    return f"{env('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')}/chat/completions", headers


def chat(sel: LLMSelection, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
    sel.validate()
    cfg = app_config()["llm"]
    url, headers = _endpoint(sel)
    body = {"model": sel.model, "messages": messages, "temperature": cfg["temperature"]}
    if tools:
        body["tools"] = tools
    if sel.provider == "openrouter":
        body["usage"] = {"include": True}       # returns cost in usage.cost
    start = time.perf_counter()
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=cfg["request_timeout_s"])
    except httpx.TimeoutException as e:
        raise LLMError("model call timed out") from e
    except httpx.HTTPError as e:
        raise LLMError(f"could not reach {sel.provider}: {type(e).__name__}") from e
    latency_ms = int((time.perf_counter() - start) * 1000)
    if r.status_code >= 400:
        retryable = r.status_code >= 500 or r.status_code in (408, 429)
        raise LLMError(f"{sel.provider} returned {r.status_code}: {_error_text(r)}", retryable=retryable)
    data = r.json()
    if "choices" not in data:
        raise LLMError(f"{sel.provider} error: {data.get('error', data)}")
    message = data["choices"][0]["message"]
    if message.get("content"):
        message["content"] = THINK_BLOCK.sub("", message["content"]).strip()
    usage = data.get("usage") or {}
    return LLMResponse(
        message={k: v for k, v in message.items() if k in ("role", "content", "tool_calls")},
        tokens_in=usage.get("prompt_tokens", 0),
        tokens_out=usage.get("completion_tokens", 0),
        cost_usd=usage.get("cost", 0.0 if sel.provider == "ollama" else None),
        latency_ms=latency_ms,
    )


def _error_text(r: httpx.Response) -> str:
    try:
        err = r.json().get("error")
        return err.get("message", str(err)) if isinstance(err, dict) else str(err)
    except ValueError:
        return r.text[:200]


def list_models(provider: str) -> list[dict]:
    """Models that support tool calling, for the UI's model picker."""
    if provider == "ollama":
        r = httpx.get(f"{env('OLLAMA_BASE_URL', 'http://localhost:11434')}/api/tags", timeout=5)
        r.raise_for_status()
        return [{"id": m["name"], "name": m["name"]} for m in r.json().get("models", [])
                if "tools" in m.get("capabilities", ["tools"])]
    if provider == "openrouter":
        r = httpx.get(f"{env('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')}/models",
                      params={"supported_parameters": "tools"}, timeout=10)
        r.raise_for_status()
        return sorted(({"id": m["id"], "name": m.get("name", m["id"])} for m in r.json()["data"]),
                      key=lambda m: m["name"].lower())
    raise LLMError(f"unknown provider {provider!r}")
