"""Loads everything under config/ plus environment variables from .env.

Prompts and policy live in config/, never in code (team working agreement).

The files in the repo are the defaults. A file edited in the /admin page is
saved to Supabase (`config_files`, with every save kept in
`config_revisions`) and that version wins until it is reverted. Edits apply
without a redeploy: the admin save calls reload(), and the server's sync
thread calls refresh_overrides() to pick up edits made in the Supabase
dashboard.
"""
from __future__ import annotations

import os
import re
import threading
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
PROMPTS_DIR = CONFIG_DIR / "prompts"

load_dotenv(ROOT / ".env")

# Files the admin page may edit or create: the three YAML files and prompts.
EDITABLE = re.compile(r"^(app|policy|guardrails)\.yaml$|^prompts/[a-z0-9_]+/[A-Za-z0-9_.-]+\.(md|txt)$")

_overrides: dict[str, str] | None = None
_overrides_lock = threading.Lock()
_dependents = []   # cache_clear functions of caches built from config


def config_cache(fn):
    """lru_cache that is cleared whenever the config changes (reload())."""
    cached = lru_cache(fn)
    _dependents.append(cached.cache_clear)
    return cached


def _fetch_overrides() -> dict[str, str]:
    from src.tools.store import get_store   # store imports this module
    try:
        return {r["path"]: r["content"] for r in get_store().config_overrides()}
    except Exception as e:   # Supabase down: run on the repo defaults
        print(f"[config] could not load edits from Supabase, using repo files: {type(e).__name__}")
        return {}


def overrides() -> dict[str, str]:
    global _overrides
    with _overrides_lock:
        if _overrides is None:
            _overrides = _fetch_overrides()
        return _overrides


def reload() -> None:
    """Drop every cached config value; the next read uses the latest edits."""
    global _overrides
    with _overrides_lock:
        _overrides = None
    for clear in _dependents:
        clear()


def refresh_overrides() -> bool:
    """Re-read edits from Supabase; reload if any changed. Returns whether they did."""
    fresh = _fetch_overrides()
    if fresh == overrides():
        return False
    reload()
    return True


def repo_file(rel: str) -> str | None:
    path = CONFIG_DIR / rel
    return path.read_text(encoding="utf-8") if path.is_file() else None


def read_config_file(rel: str) -> str:
    """Current text of a config file: the admin edit if there is one, else the repo file."""
    text = overrides().get(rel)
    if text is None:
        text = repo_file(rel)
    if text is None:
        raise FileNotFoundError(f"config/{rel}")
    return text


def config_files() -> list[str]:
    """Every editable file: the repo's plus any created in the admin page."""
    repo = [p.relative_to(CONFIG_DIR).as_posix() for p in CONFIG_DIR.rglob("*") if p.is_file()]
    return sorted({f for f in repo + list(overrides()) if EDITABLE.match(f)})


def _load_yaml(name: str) -> dict:
    return yaml.safe_load(read_config_file(name)) or {}


@config_cache
def app_config() -> dict:
    return _load_yaml("app.yaml")


@config_cache
def policy() -> dict:
    return _load_yaml("policy.yaml")


@config_cache
def guardrails_config() -> dict:
    return _load_yaml("guardrails.yaml")


def load_prompt(relative_path: str) -> str:
    return read_config_file(f"prompts/{relative_path}")


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default
