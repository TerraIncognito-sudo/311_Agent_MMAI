"""Loads everything under config/ plus environment variables from .env.

Prompts and policy live in config/, never in code (team working agreement).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
PROMPTS_DIR = CONFIG_DIR / "prompts"

load_dotenv(ROOT / ".env")


def _load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def app_config() -> dict:
    return _load_yaml("app.yaml")


@lru_cache
def policy() -> dict:
    return _load_yaml("policy.yaml")


@lru_cache
def guardrails_config() -> dict:
    return _load_yaml("guardrails.yaml")


def load_prompt(relative_path: str) -> str:
    return (PROMPTS_DIR / relative_path).read_text(encoding="utf-8")


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default
