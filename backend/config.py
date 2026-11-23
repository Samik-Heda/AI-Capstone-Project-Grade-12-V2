"""Loads config.toml (the single source of truth for ports, models and flags)."""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


@lru_cache(maxsize=1)
def load() -> dict:
    with open(ROOT / "config.toml", "rb") as f:
        cfg = tomllib.load(f)
    # Test hook: GC_OLLAMA_URL points the app at another (or a dead) Ollama, e.g. for the offline
    # UI walkthrough step. Normal runs never set it, so config.toml stays the single source.
    if os.environ.get("GC_OLLAMA_URL"):
        cfg["ollama"]["base_url"] = os.environ["GC_OLLAMA_URL"]
    return cfg


def path(key: str) -> Path:
    return ROOT / load()["paths"][key]
