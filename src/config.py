"""Loads the agent's configuration from Supabase, plus environment variables from .env.

Every config file (app.yaml, policy.yaml, guardrails.yaml, flow.yaml and the
prompts under prompts/) lives ONLY in Supabase `config_files`; every save is
kept in `config_revisions`. There is no copy in the repo for the site to fall
back on, so nothing can go stale. Edit them in /admin.

- An /admin save calls reload(), so new conversations use it at once.
- The server's sync thread calls refresh() to pick up edits made in the
  Supabase dashboard.
- If Supabase is unreachable, the last version loaded keeps being used.

Tests and offline runs (USE_MEMORY_STORE=1) use the in-memory store, which
starts from the fixture in tests/fixtures/config/. The site never reads it.
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
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "config"

load_dotenv(ROOT / ".env")

# Config files: the four YAML files and prompts/<folder>/<name>.md|.txt.
EDITABLE = re.compile(r"^(app|policy|guardrails|flow)\.yaml$|^prompts/[a-z0-9_]+/[A-Za-z0-9_.-]+\.(md|txt)$")


class ConfigUnavailable(RuntimeError):
    """Supabase couldn't be reached and no config has been loaded yet."""


_files: dict[str, str] | None = None
_lock = threading.Lock()
_dependents = []   # cache_clear functions of caches built from config


def config_cache(fn):
    """lru_cache that is cleared whenever the config changes (reload())."""
    cached = lru_cache(fn)
    _dependents.append(cached.cache_clear)
    return cached


def _fetch() -> dict[str, str]:
    from src.tools.store import get_store   # store imports this module
    return {r["path"]: r["content"] for r in get_store().list_config()}


def files() -> dict[str, str]:
    """Every config file by path, as last loaded from Supabase."""
    global _files
    with _lock:
        if _files is None:
            try:
                _files = _fetch()
            except Exception as e:
                raise ConfigUnavailable(f"could not load the configuration from Supabase: {type(e).__name__}") from e
        return _files


def reload() -> None:
    """Drop every cached config value; the next read fetches from Supabase."""
    global _files
    with _lock:
        _files = None
    for clear in _dependents:
        clear()


def refresh() -> bool:
    """Re-read config from Supabase; reload if anything changed. Returns whether it did.
    On failure the current version stays in use."""
    global _files
    fresh = _fetch()
    with _lock:
        if fresh == _files:
            return False
        _files = fresh
    for clear in _dependents:
        clear()
    return True


def read_config_file(path: str) -> str:
    text = files().get(path)
    if text is None:
        raise FileNotFoundError(f"config file {path!r} is not in Supabase config_files")
    return text


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


@config_cache
def flow_config() -> dict:
    return _load_yaml("flow.yaml")


def load_prompt(relative_path: str) -> str:
    return read_config_file(f"prompts/{relative_path}")


def fixture_files() -> dict[str, str]:
    """The test fixture (tests and USE_MEMORY_STORE=1 only)."""
    return {p.relative_to(FIXTURE_DIR).as_posix(): p.read_text(encoding="utf-8")
            for p in FIXTURE_DIR.rglob("*") if p.is_file() and EDITABLE.match(p.relative_to(FIXTURE_DIR).as_posix())}


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default
