"""Web server for the chat site (owner: E). Run: python run.py serve

Serves the static chat page, the /admin page (src/web/admin.py) and a small
JSON API. The OpenRouter key arrives in the X-OpenRouter-Key header of each
/api/chat request, is used for that one model call, and is never stored,
logged or echoed back.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.agent.orchestrator import SetupError, handle_turn
from src.config import app_config
from src.models.llm import LLMSelection, list_models
from src.rag.ingest import start_sync
from src.tools.store import MemoryStore, get_store
from src.web import admin

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app):
    # Knowledge-base and config edits made in Supabase go live without a redeploy.
    stop = start_sync(app_config()["retrieval"].get("sync_interval_s", 0))
    yield
    if stop:
        stop.set()


app = FastAPI(title="Gilead 311 Agent", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(admin.router)


class ChatRequest(BaseModel):
    interaction_id: str | None = None
    message: str = Field(min_length=1, max_length=2000)
    provider: str
    model: str


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/admin", dependencies=[Depends(admin._enabled)])
def admin_page():
    return FileResponse(STATIC / "admin.html")


@app.get("/api/config")
def site_config():
    cfg = app_config()
    return {"site": cfg["site"], "providers": cfg["llm"]["providers"],
            "default_provider": cfg["llm"]["default_provider"], "default_models": cfg["llm"]["default_models"]}


@app.get("/api/health")
def health():
    return {"ok": True, "store": "memory" if isinstance(get_store(), MemoryStore) else "supabase"}


@app.get("/api/models")
def models(provider: str):
    if provider not in app_config()["llm"]["providers"]:
        raise HTTPException(400, f"unknown provider {provider!r}")
    try:
        return {"models": list_models(provider)}
    except Exception as e:
        raise HTTPException(502, f"could not list {provider} models ({type(e).__name__})")


@app.post("/api/chat")
def chat(req: ChatRequest, x_openrouter_key: str | None = Header(default=None)):
    sel = LLMSelection(provider=req.provider, model=req.model,
                       api_key=x_openrouter_key if req.provider == "openrouter" else None)
    try:
        return handle_turn(req.interaction_id, req.message, sel)
    except SetupError as e:
        raise HTTPException(400, str(e))
