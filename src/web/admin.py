"""Admin API behind the /admin page (owner: E). No login yet: prototype only.

Set ADMIN_ENABLED=0 in .env to switch the page and this API off.

- Interactions: live walkthrough of a conversation from interaction_events,
  handoffs and tickets. Everything returned here is masked (D6), as in the logs.
- Files: edit config/ files in the browser. A save is checked first (YAML
  parses, keys the code reads are still there, regexes compile, the system
  prompt renders), then stored in Supabase with its history and applied
  without a redeploy (src/config.py).
- Knowledge base: edit kb_documents; changed documents are re-chunked at once.
"""
from __future__ import annotations

import json
import re

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.agent.orchestrator import render_system_prompt
from src.config import (EDITABLE, app_config, config_files, env, overrides, policy, read_config_file,
                        reload, repo_file)
from src.guardrails.masking import mask_obj
from src.rag.ingest import chunk_stale
from src.tools.store import StoreError, get_store

MAX_FILE_CHARS = 200_000

# Sections that are lists of entries: teammates may add or remove entries.
# Everywhere else, every key in the repo version (two levels deep) is read by
# the code and must stay.
COLLECTIONS = {("policy.yaml", "service_catalogue"), ("policy.yaml", "rules"), ("policy.yaml", "change_log"),
               ("guardrails.yaml", "redaction"), ("guardrails.yaml", "masking")}
CATALOGUE_KEYS = ("category", "unit", "required_fields", "escalation_triggers", "duplicate_window")


def _enabled():
    if env("ADMIN_ENABLED", "1") != "1":
        raise HTTPException(404)


router = APIRouter(prefix="/api/admin", dependencies=[Depends(_enabled)])


def _store_call(fn, *args):
    try:
        return fn(*args)
    except StoreError as e:
        raise HTTPException(502, f"database unavailable: {e}")


# ---------------------------------------------------------------- interactions

@router.get("/interactions")
def interactions(limit: int = 50):
    return {"interactions": _store_call(get_store().list_interactions, min(limit, 200))}


@router.get("/interactions/{interaction_id}")
def interaction(interaction_id: str):
    store = get_store()
    events = _store_call(store.interaction_events, interaction_id)
    for e in events:
        try:
            e["data"] = json.loads(e["text"]) if e.get("text", "").startswith("{") else None
        except (json.JSONDecodeError, AttributeError):
            e["data"] = None
    ticket_ids = sorted({(e.get("data") or {}).get("result", {}).get("ticket_id")
                         for e in events if e["step"] == "tool:create_ticket"} - {None})
    return {
        "interaction_id": interaction_id,
        "events": events,
        "handoffs": _store_call(store.handoffs_for, interaction_id),
        "tickets": mask_obj(_store_call(store.tickets_by_id, ticket_ids)),
    }


@router.get("/rules")
def rules():
    pol = policy()
    return {"version": pol.get("version"), "rules": pol.get("rules", {}),
            "catalogue": {c: e["category"] for c, e in pol["service_catalogue"].items()}}


# ---------------------------------------------------------------- config files

class FileSave(BaseModel):
    content: str = Field(max_length=MAX_FILE_CHARS)
    author: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=500)


def _check_path(path: str):
    if not EDITABLE.match(path):
        raise HTTPException(400, "only app.yaml, policy.yaml, guardrails.yaml and prompts/<folder>/<name>.md|.txt can be edited")


def _missing_keys(path: str, repo: dict, new: dict) -> list[str]:
    missing = []
    for key, value in repo.items():
        if key not in new:
            missing.append(key)
        elif isinstance(value, dict) and (path, key) not in COLLECTIONS and isinstance(new[key], dict):
            missing += [f"{key}.{k}" for k in value if k not in new[key]]
    return missing


def _compile_all(obj, where: str, errors: list[str]):
    if isinstance(obj, str):
        try:
            re.compile(obj)
        except re.error as e:
            errors.append(f"{where}: bad regular expression ({e})")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _compile_all(v, f"{where}.{k}", errors)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _compile_all(v, f"{where}[{i}]", errors)


def check_config(path: str, content: str) -> list[str]:
    """Problems that would break the agent if this edit were saved. Empty = safe."""
    files = {f: read_config_file(f) for f in ("app.yaml", "policy.yaml", "guardrails.yaml")}
    files[path] = content
    errors, parsed = [], {}
    for name in ("app.yaml", "policy.yaml", "guardrails.yaml"):
        try:
            parsed[name] = yaml.safe_load(files[name])
        except yaml.YAMLError as e:
            errors.append(f"{name}: not valid YAML ({e})")
            continue
        if not isinstance(parsed[name], dict):
            errors.append(f"{name}: must be a YAML mapping (key: value)")
            continue
        repo = yaml.safe_load(repo_file(name) or "") or {}
        missing = _missing_keys(name, repo, parsed[name])
        if missing:
            errors.append(f"{name}: the code reads these keys, which are missing: {', '.join(missing)}")
    if errors:
        return errors

    app, pol, guards = parsed["app.yaml"], parsed["policy.yaml"], parsed["guardrails.yaml"]
    for code, entry in (pol.get("service_catalogue") or {}).items():
        lacking = [k for k in CATALOGUE_KEYS if k not in (entry or {})]
        if lacking:
            errors.append(f"policy.yaml: service_catalogue.{code} is missing {', '.join(lacking)}")
    _compile_all(guards, "guardrails.yaml", errors)
    unknown = set(app["llm"].get("providers") or []) - {"ollama", "openrouter"}
    if unknown:
        errors.append(f"app.yaml: llm.providers can only be ollama and openrouter, not {', '.join(sorted(unknown))}")

    prompt_path = f"prompts/{app['agent']['system_prompt']}"
    template = content if path == prompt_path else (overrides().get(prompt_path) or repo_file(prompt_path))
    if template is None:
        errors.append(f"app.yaml: agent.system_prompt points to config/{prompt_path}, which doesn't exist")
    elif not errors:
        try:
            render_system_prompt(pol, template)
        except KeyError as e:
            errors.append(f"{prompt_path}: placeholder {{{e.args[0]}}} has no value. Use the placeholders listed "
                          "in the prompt's header, or {{ }} for a literal brace")
        except (ValueError, IndexError) as e:
            errors.append(f"{prompt_path}: the template can't be filled in ({e}). Write {{{{ }}}} for a literal brace")
    return errors


@router.get("/files")
def files():
    edits = {r["path"]: r for r in _store_call(get_store().config_overrides)}
    return {"files": [{
        "path": f,
        "edited": f in edits,
        "in_repo": repo_file(f) is not None,
        "updated_at": edits.get(f, {}).get("updated_at"),
        "updated_by": edits.get(f, {}).get("updated_by"),
    } for f in config_files()]}


@router.get("/file")
def get_file(path: str):
    _check_path(path)
    repo = repo_file(path)
    live = overrides().get(path)
    if live is None and repo is None:
        raise HTTPException(404, f"config/{path} doesn't exist")
    return {"path": path, "content": live if live is not None else repo, "repo_content": repo,
            "in_repo": repo is not None, "edited": live is not None, "in_use": path in _in_use()}


def _in_use() -> set[str]:
    return {"app.yaml", "policy.yaml", "guardrails.yaml", f"prompts/{app_config()['agent']['system_prompt']}"}


@router.put("/file")
def save_file(path: str, body: FileSave):
    _check_path(path)
    errors = check_config(path, body.content)
    if errors:
        raise HTTPException(422, {"errors": errors})
    _store_call(get_store().save_config, path, body.content, body.author, body.note)
    reload()
    return {"ok": True, "path": path}


@router.delete("/file")
def revert_file(path: str, author: str | None = None):
    _check_path(path)
    repo = repo_file(path)
    if repo is not None:
        errors = check_config(path, repo)
        if errors:
            raise HTTPException(422, {"errors": errors})
    elif path in _in_use():
        raise HTTPException(422, {"errors": [f"config/{path} is in use and has no repo version to go back to"]})
    _store_call(get_store().revert_config, path, author)
    reload()
    return {"ok": True, "path": path}


@router.get("/revisions")
def revisions(path: str):
    _check_path(path)
    return {"revisions": _store_call(get_store().config_revisions, path)}


# ---------------------------------------------------------------- knowledge base

class KbDocument(BaseModel):
    source: str | None = Field(default=None, min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=MAX_FILE_CHARS)
    active: bool = True


@router.get("/kb")
def kb_list():
    return {"documents": _store_call(get_store().list_documents)}


def _rechunk():
    try:
        chunk_stale(get_store())
    except Exception as e:   # the sync thread will retry
        print(f"[admin] re-chunk failed, sync will retry: {type(e).__name__}")


@router.post("/kb")
def kb_create(doc: KbDocument):
    if not doc.source:
        doc.source = re.sub(r"[^a-z0-9]+", "_", doc.title.lower()).strip("_")
    if any(d["source"] == doc.source for d in _store_call(get_store().list_documents)):
        raise HTTPException(409, f"a document with source {doc.source!r} already exists")
    row = _store_call(get_store().upsert_document, doc.model_dump())
    _rechunk()
    return {"document": row}


@router.put("/kb/{document_id}")
def kb_update(document_id: str, doc: KbDocument):
    row = _store_call(get_store().update_document, document_id, doc.model_dump(exclude_none=True))
    if row is None:
        raise HTTPException(404, "document not found")
    _rechunk()
    return {"document": row}


@router.delete("/kb/{document_id}")
def kb_delete(document_id: str):
    _store_call(get_store().delete_document, document_id)
    return {"ok": True}
